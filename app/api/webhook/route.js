import { NextResponse } from 'next/server';

export async function POST(request) {
  try {
    const body = await request.json();
    console.log("Received n8n payload:", body);

    // Process your n8n payload here
    return NextResponse.json({ success: true, data: body });
  } catch (error) {
    return NextResponse.json({ success: false, error: error.message }, { status: 500 });
  }
}

export async function GET() {
  return NextResponse.json({ message: "Webhook bridge is active" });
}
