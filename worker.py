import os
import sys
import json
import tempfile
import yt_dlp
from openai import OpenAI
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
AGENTROUTER_API_KEY = os.environ.get("AGENTROUTER_API_KEY")

if not all([SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, OPENAI_API_KEY, AGENTROUTER_API_KEY]):
    print("[!] Missing environment variables.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
openai_client = OpenAI(api_key=OPENAI_API_KEY)
agentrouter_client = OpenAI(
    api_key=AGENTROUTER_API_KEY,
    base_url="https://agentrouter.org/v1"
)

def download_audio(video_url: str, output_dir: str) -> str:
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])
    return os.path.join(output_dir, "audio.mp3")

def analyze_with_deepseek(transcript_text: str) -> list:
    prompt = f"""
    You are an expert viral content editor. Analyze the following video transcript and identify ALL potential viral clip moments.
    Do not restrict yourself to a set number—extract as many high-value clips as the content naturally justifies.

    Return ONLY a JSON array with objects containing:
    - "title": A short catchy title for the clip
    - "start": Start time in seconds
    - "end": End time in seconds
    - "text": Segment text
    - "score": Virality score (0-100)

    Transcript:
    {transcript_text}
    """
    response = agentrouter_client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    res_data = json.loads(response.choices[0].message.content)
    return res_data.get("clips", res_data) if isinstance(res_data, dict) else res_data

def process_video_job(job: dict):
    job_id = job['id']
    video_url = job['video_url']

    try:
        supabase.table('clip_jobs').update({'status': 'processing'}).eq('id', job_id).execute()

        with tempfile.TemporaryDirectory() as temp_dir:
            print("[1/3] Downloading audio...")
            audio_path = download_audio(video_url, temp_dir)

            print("[2/3] Transcribing audio with OpenAI Whisper...")
            with open(audio_path, "rb") as audio_file:
                transcript = openai_client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file,
                    response_format="verbose_json"
                )

            print("[3/3] Analyzing dynamic viral hooks using DeepSeek-V4-Flash via AgentRouter...")
            clips_output = analyze_with_deepseek(transcript.text)

            supabase.table('clip_jobs').update({
                'status': 'completed',
                'clips': clips_output,
                'error_message': None
            }).eq('id', job_id).execute()

            print(f"[✓] Job {job_id} successfully completed!")

    except Exception as e:
        print(f"[!] Job failed: {str(e)}")
        supabase.table('clip_jobs').update({
            'status': 'failed',
            'error_message': str(e)
        }).eq('id', job_id).execute()

def main():
    print("🚀 Worker active! Checking Supabase jobs...")
    response = supabase.table('clip_jobs').select('*').eq('status', 'pending').limit(1).execute()
    if response.data:
        process_video_job(response.data[0])
    else:
        print("No pending jobs found.")

if __name__ == "__main__":
    main()
