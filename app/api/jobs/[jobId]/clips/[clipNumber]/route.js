import { NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';

export async function GET(request, { params }) {
  try {
    const supabase = await createClient();
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) return NextResponse.json({ error: 'Authentication required' }, { status: 401 });
    const { jobId, clipNumber } = await params;
    if (!/^[0-9a-f-]{36}$/i.test(jobId) || !/^[1-3]$/.test(clipNumber)) {
      return NextResponse.json({ error: 'Clip not found' }, { status: 404 });
    }
    // Explicit ownership filter even before RLS is reconciled by migration 002.
    const { data: job, error } = await supabase.from('clip_jobs').select('id,status,clips')
      .eq('id', jobId).eq('user_id', user.id).maybeSingle();
    if (error) return NextResponse.json({ error: 'Unable to load clip' }, { status: 500 });
    const clip = job?.status === 'completed' && Array.isArray(job.clips)
      && job.clips.find((item) => item.clip_number === Number(clipNumber));
    const bucket = process.env.CLIPS_STORAGE_BUCKET || 'clips';
    // Sign only a path in this owner's job, never a browser-supplied path.
    const escapedPrefix = `${user.id}/${jobId}/`;
    if (!clip || clip.storage_bucket !== bucket || typeof clip.object_path !== 'string'
      || !clip.object_path.startsWith(escapedPrefix)
      || !/^attempt-[1-9]\d*\/clip-[1-3]\.mp4$/.test(clip.object_path.slice(escapedPrefix.length))) {
      return NextResponse.json({ error: 'Clip not found' }, { status: 404 });
    }
    const download = new URL(request.url).searchParams.get('download') === '1';
    const { data, error: signError } = await supabase.storage.from(bucket)
      .createSignedUrl(clip.object_path, 300, download ? { download: `clip-${clipNumber}.mp4` } : {});
    if (signError || !data?.signedUrl) return NextResponse.json({ error: 'Unable to open clip' }, { status: 503 });
    return NextResponse.json({ url: data.signedUrl, expiresIn: 300 }, {
      headers: { 'Cache-Control': 'private, no-store' },
    });
  } catch {
    return NextResponse.json({ error: 'Unable to open clip' }, { status: 500 });
  }
}
