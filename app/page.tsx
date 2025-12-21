import { createServerClient } from '@/app/lib/supabase-server';
import { redirect } from 'next/navigation';
import VerificationDashboard from '@/app/components/VerificationDashboard';

export default async function Home() {
  const supabase = createServerClient();

  const {
    data: { session },
  } = await supabase.auth.getSession();

  if (!session) {
    redirect('/auth/signin');
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <VerificationDashboard />
    </div>
  );
}
