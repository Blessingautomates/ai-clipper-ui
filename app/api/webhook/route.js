import { NextResponse } from 'next/server';

export async function POST(request) {
  try {
    const body = await request.json();

    const n8nResponse = await fetch("https://nextgenzauto.app.n8n.cloud/webhook/generate-clips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

    const responseText = await n8nResponse.text();

    if (!n8nResponse.ok) {
      return NextResponse.json(
        { success: false, error: `n8n Error (${n8nResponse.status}): ${responseText}` },
        { status: n8nResponse.status }
      );
    }

    let parsedData;
    try {
      parsedData = JSON.parse(responseText);
    } catch {
      parsedData = responseText;
    }

    return NextResponse.json({ success: true, data: parsedData });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ message: "Webhook bridge is active" });
}
