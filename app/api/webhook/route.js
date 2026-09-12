import { NextResponse } from 'next/server';

export async function POST(request) {
  try {
    const body = await request.json();

    const n8nResponse = await fetch("https://nextgenzauto.app.n8n.cloud/webhook/generate-clips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    return NextResponse.json({ success: true, status: n8nResponse.status });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ message: "Webhook bridge is active" });
}
