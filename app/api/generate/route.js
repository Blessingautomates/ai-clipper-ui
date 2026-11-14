import { NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';
import { dispatchWorker } from '@/lib/worker-dispatch';

export async function POST(request) {
  try {
    const supabase = await createClient();
    const { data: { user }, error: authError } = await supabase.auth.getUser();

    if (authError || !user) {
      return NextResponse.json({ error: 'Authentication required' }, { status: 401 });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: 'Invalid JSON request' }, { status: 400 });
    }
    const videoUrl = body?.videoUrl;

    let parsed;
    try {
      parsed = typeof videoUrl === 'string' && new URL(videoUrl.trim());
    } catch { /* handled below */ }
    if (!parsed || !['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || videoUrl.length > 4096) {
      return NextResponse.json(
        { error: 'Please provide a valid video URL (e.g. https://...)' },
        { status: 400 }
      );
    }

    // Ownership comes from the verified cookie session. Only these two columns
    // are insertable by authenticated; queue state uses database defaults.
    const { data, error } = await supabase
      .from('clip_jobs')
      .insert([
        {
          user_id: user.id,
          video_url: videoUrl.trim(),
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

    const workerDispatch = await dispatchWorker();
    return NextResponse.json({
      success: true,
      message: 'Video job queued successfully',
      job: data,
      workerDispatch,
    });
  } catch {
    return NextResponse.json(
      { error: 'Server error processing request' },
      { status: 500 }
    );
  }
}
