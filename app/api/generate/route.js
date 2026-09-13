import { NextResponse } from "next/server";

export async function POST(req) {
  try {
    const body = await req.json();
    const { mode, url, fileUrl, userId, transcript } = body;

    const apiKey = process.env.AGENTROUTER_API_KEY;
    const baseUrl = process.env.AGENTROUTER_BASE_URL || "https://agentrouter.org/v1";
    const n8nWebhookUrl = process.env.N8N_WEBHOOK_URL;

    // 1. Analyze Transcript using DeepSeek-V4-flash via AgentRouter
    let analyzedClips = [];
    
    if (transcript) {
      const prompt = `
You are a viral video strategist. Analyze this transcript with timestamps:
"${transcript}"

Extract 2-3 viral short-form video clips (30-60 seconds long).
Return ONLY a raw JSON array matching this exact schema, without markdown codeblocks:
[
  {
    "title": "Catchy Title",
    "start_time": 10.5,
    "end_time": 45.0,
    "score": 95,
    "caption": "Short viral caption summarizing the clip"
  }
]
`;

      const aiResponse = await fetch(`${baseUrl}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          model: "deepseek-V4-flash",
          messages: [{ role: "user", content: prompt }],
          temperature: 0.3
        })
      });

      if (!aiResponse.ok) {
        throw new Error(`AgentRouter API Error: ${aiResponse.statusText}`);
      }

      const aiData = await aiResponse.json();
      const rawText = aiData.choices[0].message.content.trim();
      analyzedClips = JSON.parse(rawText.replace(/```json|```/g, ""));
    }

    // 2. Pass video source & analyzed clips to n8n webhook for media processing
    const n8nResponse = await fetch(n8nWebhookUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode,
        videoSource: mode === "url" ? url : fileUrl,
        userId: userId || "guest_user",
        clips: analyzedClips,
        timestamp: new Date().toISOString()
      })
    });

    if (!n8nResponse.ok) {
      throw new Error(`n8n Webhook Error: Status ${n8nResponse.status}`);
    }

    const result = await n8nResponse.json();
    return NextResponse.json({ success: true, data: result });

  } catch (error) {
    return NextResponse.json(
      { error: error.message || "Failed to process video pipeline." },
      { status: 500 }
    );
  }
}
