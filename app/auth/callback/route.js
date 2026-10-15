import { createClient } from '@/utils/supabase/server';
import { NextResponse } from 'next/server';

// Handles both the Google OAuth redirect and the email-confirmation link.
// Both arrive here with a `code` that must be exchanged for a session.
//
// IMPORTANT: this must use the cookie-backed server client. The previous
// version used the plain browser client from `lib/supabaseClient`, which
// stores the session in memory only -- so no auth cookie was ever written
// and the user landed on /dashboard logged out. `createClient()` here reads
// and writes the request/response cookie jar, so the session persists.
export async function GET(request) {
  const requestUrl = new URL(request.url);
  const code = requestUrl.searchParams.get('code');
  const next = requestUrl.searchParams.get('next') ?? '/dashboard';

  if (!code) {
    return NextResponse.redirect(
      new URL('/login?error=missing_code', request.url)
    );
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.exchangeCodeForSession(code);

  if (error) {
    const url = new URL('/login', request.url);
    url.searchParams.set('error', error.message);
    return NextResponse.redirect(url);
  }

  // Only allow same-site relative redirects, so `next` cannot be used as an
  // open-redirect to an attacker-controlled host.
  const safeNext =
    next.startsWith('/') && !next.startsWith('//') ? next : '/dashboard';
  return NextResponse.redirect(new URL(safeNext, request.url));
}
