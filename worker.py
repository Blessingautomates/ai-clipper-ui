import os
import sys
import time
import tempfile
import yt_dlp
from openai import OpenAI
from supabase import create_client, Client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY or not OPENAI_API_KEY:
    print("[!] Missing environment variables! Check GitHub Secrets configuration.")
    sys.exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
client = OpenAI(api_key=OPENAI_API_KEY)


def download_audio(video_url: str, output_dir: str) -> str:
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_template,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([video_url])

    return os.path.join(output_dir, "audio.mp3")


def process_video_job(job: dict):
    job_id = job['id']
    video_url = job['video_url']

    print(f"\n[+] Picked up Job ID: {job_id}")

    try:
        supabase.table('clip_jobs').update({'status': 'processing'}).eq('id', job_id).execute()

        with tempfile.TemporaryDirectory() as temp_dir:
            print("[1/2] Downloading audio file...")
            audio_path = download_audio(video_url, temp_dir)

            print("[2/2] Transcribing with OpenAI Whisper API...")
            with open(audio_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1", 
                    file=audio_file,
                    response_format="verbose_json"
                )

            segments = getattr(transcript, 'segments', [])
            clips_output = []
            
            for idx, segment in enumerate(segments[:3]):
                clips_output.append({
                    "id": f"clip_{idx+1}",
                    "title": f"Viral Moment {idx+1}",
                    "start": round(segment.get("start", 0), 2),
                    "end": round(segment.get("end", 0), 2),
                    "text": segment.get("text", "").strip(),
                    "score": 95 - (idx * 5)
                })

            supabase.table('clip_jobs').update({
                'status': 'completed',
                'clips': clips_output,
                'error_message': None
            }).eq('id', job_id).execute()

            print(f"[✓] Successfully completed job {job_id}!")

    except Exception as e:
        print(f"[!] Error processing job {job_id}: {str(e)}")
        supabase.table('clip_jobs').update({
            'status': 'failed',
            'error_message': str(e)
        }).eq('id', job_id).execute()


def main():
    print("🚀 Worker active! Polling Supabase for pending video clip jobs...")
    response = supabase.table('clip_jobs').select('*').eq('status', 'pending').limit(1).execute()
    jobs = response.data

    if jobs:
        process_video_job(jobs[0])
    else:
        print("No pending jobs found.")


if __name__ == "__main__":
    main()
