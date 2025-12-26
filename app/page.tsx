import { Suspense } from 'react';
import VerificationDashboard from '@/app/components/VerificationDashboard';
import DashboardSkeleton from '@/app/components/DashboardSkeleton';

export default function Home() {
  return (
    <div className="min-h-screen bg-gray-50">
      <Suspense fallback={<DashboardSkeleton />}>
        <VerificationDashboard />
      </Suspense>
    </div>
  );
}
