import { NextResponse } from 'next/server';

export async function POST(request) {
  try {
    const body = await request.json();

    // YOUR N8N PRODUCTION WEBHOOK URL
    const N8N_WEBHOOK_URL = "https://nextgenzauto.app.n8n.cloud/webhook/generate-clips";

    const n8nResponse = await fetch(N8N_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    if (!n8nResponse.ok) {
      throw new Error(`n8n responded with status ${n8nResponse.status}`);
    }

    const data = await n8nResponse.json();
    return NextResponse.json({ success: true, data });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ message: "Webhook bridge is active" });
}
