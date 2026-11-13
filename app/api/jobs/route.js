import { NextResponse } from 'next/server';
import { createClient } from '@/utils/supabase/server';

export async function GET() {
  try {
    const supabase = await createClient();
    const { data: { user }, error: authError } = await supabase.auth.getUser();
    if (authError || !user) return NextResponse.json({ error: 'Authentication required' }, { status: 401 });
    const { data, error } = await supabase.from('clip_jobs')
      .select('id,video_url,status,clips,error_message,created_at')
      .eq('user_id', user.id).order('created_at', { ascending: false }).limit(30);
    if (error) return NextResponse.json({ error: 'Unable to load jobs' }, { status: 500 });
    return NextResponse.json({ jobs: data }, { headers: { 'Cache-Control': 'private, no-store' } });
  } catch {
    return NextResponse.json({ error: 'Unable to load jobs' }, { status: 500 });
  }
}
