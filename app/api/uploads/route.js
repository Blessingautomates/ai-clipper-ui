import { randomUUID } from 'node:crypto';
import { createClient as createStorageClient } from '@supabase/supabase-js';
import { NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';

const MAX_UPLOAD_BYTES = 100 * 1024 * 1024;

export async function POST(request) {
  try {
    const supabase = await createClient();
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) return NextResponse.json({ error: 'Authentication required' }, { status: 401 });
    let body;
    try { body = await request.json(); } catch {
      return NextResponse.json({ error: 'Invalid JSON request' }, { status: 400 });
    }
    if (body?.contentType !== 'video/mp4' || !Number.isSafeInteger(body?.size)
      || body.size <= 0 || body.size > MAX_UPLOAD_BYTES) {
      return NextResponse.json({ error: 'Choose an MP4 up to 100 MB.' }, { status: 400 });
    }
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
    if (!url || !key) return NextResponse.json({ error: 'Video uploads are not configured.' }, { status: 503 });
    // Only this server may authorize a new object. No service key reaches the browser.
    const storage = createStorageClient(url, key, {
      auth: { persistSession: false, autoRefreshToken: false },
    }).storage;
    const bucketName = process.env.CLIPS_STORAGE_BUCKET || 'clips';
    const { data: bucket, error: bucketError } = await storage.getBucket(bucketName);
    if (bucketError || !bucket || bucket.public !== false
      || (bucket.allowed_mime_types?.length && !bucket.allowed_mime_types.includes('video/mp4'))) {
      return NextResponse.json({ error: 'Private video storage is unavailable.' }, { status: 503 });
    }
    const maxBytes = Math.min(MAX_UPLOAD_BYTES, bucket.file_size_limit ?? MAX_UPLOAD_BYTES);
    if (body.size > maxBytes) return NextResponse.json({ error: `This storage bucket accepts files up to ${Math.floor(maxBytes / 1024 / 1024)} MB.` }, { status: 400 });
    const path = `${user.id}/sources/${randomUUID()}.mp4`;
    const { data, error } = await storage.from(bucketName).createSignedUploadUrl(path, { upsert: false });
    if (error || !data?.token) return NextResponse.json({ error: 'Unable to prepare video upload.' }, { status: 503 });
    return NextResponse.json({ bucket: bucketName, path, token: data.token, maxBytes }, {
      headers: { 'Cache-Control': 'private, no-store' },
    });
  } catch {
    return NextResponse.json({ error: 'Unable to prepare video upload.' }, { status: 500 });
  }
}
