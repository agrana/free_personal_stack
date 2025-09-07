import { createServerClient } from '@/app/lib/supabase-server';
import { NextResponse } from 'next/server';

export async function POST() {
  const supabase = createServerClient();

  const { error } = await supabase.auth.signOut();

  if (error) {
    return NextResponse.json({ error: error.message }, { status: 400 });
  }

  return NextResponse.redirect(
    new URL('/', process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000')
  );
}
