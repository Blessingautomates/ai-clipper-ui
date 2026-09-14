import { createClient } from '@/lib/supabaseClient';
import { NextResponse } from 'next/server';

export async function GET(request) {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get('code');

  if (code) {
    const supabase = createClient();
    await supabase.auth.exchangeCodeForSession(code);
  }

  // Redirects back to https://clipper.toolstackai.xyz/dashboard
  return NextResponse.redirect(new URL('/dashboard', request.url));
}
