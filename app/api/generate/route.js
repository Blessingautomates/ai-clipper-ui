import { NextResponse } from 'next/server';

export async function POST(request) {
  try {
    const { videoUrl } = await request.json();

    // Validates that a string was provided and starts with http:// or https://
    if (!videoUrl || typeof videoUrl !== 'string' || !videoUrl.match(/^https?:\/\/.+/i)) {
      return NextResponse.json(
        { error: 'Please provide a valid video URL (e.g. https://...)' },
        { status: 400 }
      );
    }

    // WEBHOOK_URL points to your n8n or backend processing engine
    const webhookUrl = process.env.CLIPPER_BACKEND_WEBHOOK_URL;

    if (webhookUrl) {
      await fetch(webhookUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          videoUrl: videoUrl.trim(), 
          timestamp: new Date().toISOString() 
        }),
      });
    }

    return NextResponse.json({
      success: true,
      message: 'Video URL received and queued for processing',
      jobId: `job_${Date.now()}`,
    });
  } catch (error) {
    return NextResponse.json(
      { error: 'Failed to process video URL' },
      { status: 500 }
    );
  }
}
