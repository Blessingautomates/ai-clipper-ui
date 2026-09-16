import { NextResponse } from 'next/server';

export async function POST(req: Request) {
  try {
    const { url, chatLog } = await req.json();

    if (!url) {
      return NextResponse.json({ error: 'Video URL is required' }, { status: 400 });
    }

    const agentRouterKey = process.env.AGENTROUTER_API_KEY;

    if (!agentRouterKey) {
      return NextResponse.json(
        { error: 'AGENTROUTER_API_KEY is missing in Vercel environment variables.' },
        { status: 500 }
      );
    }

    const systemPrompt = `
You are an elite short-form video viral strategist and lead video editor.
Your objective is to analyze transcripts, video descriptions, and optional stream chat logs to extract all highly engaging short-form clips (TikTok, Shorts, Reels).

CRITICAL INSTRUCTION - DYNAMIC CLIP COUNT:
Do NOT limit output to 3 clips. Dynamically extract ALL valid viral moments available based on content length and density (ranging anywhere from 1 to 15+ clips depending on the source material). If a 2-hour podcast has 10 great moments, output 10 clips. If a 5-minute video has only 2 strong moments, output 2 clips.

ANALYSIS FRAMEWORK:
1. Multi-Genre Intelligence:
   - Podcasts/Talks: Pinpoint controversial opinions, counter-intuitive claims, emotional stories, and high-value actionable takeaways.
   - Gaming/Streams: Detect high-energy spikes, rage quits, clutch plays, or sudden chaotic banter.
   - Vlogs/IRL: Detect sudden narrative shifts, intense reactions, visual jokes, or peak emotional beats.
   - Educational/Tech: Detect concise "lightbulb moments", step-by-step hacks, or surprising facts.

2. Social Proof & Chat Analytics (If Chat Logs/Messages are provided):
   - Analyze frequency spikes, spamming of specific emojis (e.g., 😂, 💀, 🔥, KEKW, POG), or sudden message bursts to validate exact timestamps for hype/funny moments.

3. Virality Scoring (0–100 Scale):
   - Assign a Virality Score to each extracted clip based on hook strength, emotional resonance, and pacing.
   - Provide a concise 1-2 sentence rationale explaining WHY the score was assigned.

OUTPUT FORMAT:
Return strictly a valid JSON array of objects (no markdown wrappers, no introductory commentary). Use the following JSON schema:

[
  {
    "clip_number": 1,
    "start_time": "HH:MM:SS",
    "end_time": "HH:MM:SS",
    "duration_seconds": 45,
    "title": "Punchy Short Title",
    "hook_sentence": "First 3-second opening hook line",
    "virality_score": 92,
    "rationale": "Strong controversial opening hook followed by intense emotional payoff. Chat activity spiked with laughter emojis at this exact mark.",
    "genre_category": "Podcasts | Gaming | Vlog | Educational",
    "suggested_caption": "Engaging social media caption with 3-4 hashtags"
  }
]
`;

    const userContent = `
Analyze the following content and extract all viral clips:
- Video URL: ${url}
${chatLog ? `- Stream Chat Log Data: ${JSON.stringify(chatLog)}` : ''}
`;

    const response = await fetch('https://agentrouter.ai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${agentRouterKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: 'deepseek-v4-flash',
        messages: [
          { role: 'system', content: systemPrompt },
          { role: 'user', content: userContent },
        ],
        temperature: 0.3,
      }),
    });

    if (!response.ok) {
      const errText = await response.text();
      throw new Error(`AgentRouter API error (${response.status}): ${errText}`);
    }

    const data = await response.json();
    const rawContent = data.choices?.[0]?.message?.content || '[]';

    let parsedClips;
    try {
      const cleanJson = rawContent.replace(/```json|```/g, '').trim();
      parsedClips = JSON.parse(cleanJson);
    } catch {
      parsedClips = rawContent;
    }

    return NextResponse.json({
      success: true,
      url,
      total_clips: Array.isArray(parsedClips) ? parsedClips.length : 0,
      clips: parsedClips,
    });

  } catch (error: any) {
    return NextResponse.json(
      { error: error.message || 'Failed to process video analysis' },
      { status: 500 }
    );
  }
}
