import { NextResponse } from 'next/server';
import { supabase } from '@/lib/supabaseClient';

export async function POST(request) {
  try {
    const { videoUrl, userId } = await request.json();

    if (!videoUrl || typeof videoUrl !== 'string' || !videoUrl.match(/^https?:\/\/.+/i)) {
      return NextResponse.json(
        { error: 'Please provide a valid video URL (e.g. https://...)' },
        { status: 400 }
      );
    }

    // Insert new job record into Supabase clip_jobs table
    const { data, error } = await supabase
      .from('clip_jobs')
      .insert([
        {
          user_id: userId || null,
          video_url: videoUrl.trim(),
          status: 'pending',
          clips: [],
        },
      ])
      .select()
      .single();

    if (error) {
      console.error('Database insertion error:', error);
      return NextResponse.json(
        { error: 'Failed to create video processing job' },
        { status: 500 }
      );
    }

    return NextResponse.json({
      success: true,
      message: 'Video job queued successfully',
      job: data,
    });
  } catch (error) {
    return NextResponse.json(
      { error: 'Server error processing request' },
      { status: 500 }
    );
  }
}
