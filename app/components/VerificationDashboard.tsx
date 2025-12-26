'use client';

import { useEffect, useState, lazy, Suspense } from 'react';
import { useRouter } from 'next/navigation';
import { createClient } from '@/app/lib/supabase';
import ConfigurationCheck from './ConfigurationCheck';

// Lazy load verification components to improve initial render
const DomainVerification = lazy(() => import('./DomainVerification'));
const AuthVerification = lazy(() => import('./AuthVerification'));
const DatabaseVerification = lazy(() => import('./DatabaseVerification'));
const EmailRoutingVerification = lazy(() => import('./EmailRoutingVerification'));

const VerificationSkeleton = () => (
  <div className="bg-white rounded-lg shadow-sm border p-6 animate-pulse">
    <div className="h-6 w-40 bg-gray-200 rounded mb-4" />
    <div className="space-y-3">
      <div className="h-4 w-full bg-gray-200 rounded" />
      <div className="h-4 w-3/4 bg-gray-200 rounded" />
    </div>
  </div>
);

interface VerificationStatus {
  domain: 'checking' | 'success' | 'error';
  auth: 'checking' | 'success' | 'error';
  database: 'checking' | 'success' | 'error';
  email: 'checking' | 'success' | 'error';
}

export default function VerificationDashboard() {
  const router = useRouter();
  const [statuses, setStatuses] = useState<VerificationStatus>({
    domain: 'checking',
    auth: 'checking',
    database: 'checking',
    email: 'checking',
  });
  const [configError, setConfigError] = useState<string | null>(null);
  const [authChecked, setAuthChecked] = useState(false);

  // Check authentication on mount
  useEffect(() => {
    const checkAuth = async () => {
      const supabase = createClient();
      const {
        data: { session },
      } = await supabase.auth.getSession();
      
      if (!session) {
        router.push('/auth/signin');
        return;
      }
      
      setAuthChecked(true);
    };

    checkAuth();
  }, [router]);

  const allSuccess = Object.values(statuses).every(
    (status) => status === 'success'
  );
  const anyError = Object.values(statuses).some((status) => status === 'error');

  // Show loading state while checking auth
  if (!authChecked) {
    return (
      <div className="max-w-6xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-gray-900"></div>
          <p className="mt-4 text-gray-600">Checking authentication...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-6xl mx-auto py-8 px-4 sm:px-6 lg:px-8">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-2">
          Infrastructure Verification Dashboard
        </h1>
        <p className="text-gray-600">
          Verify that all your infrastructure components are properly configured
          and working.
        </p>
      </div>

      {/* Configuration Check */}
      <ConfigurationCheck onError={setConfigError} />

      {configError && (
        <div className="mb-6 p-4 bg-red-50 rounded-lg border border-red-200">
          <p className="text-sm text-red-800">{configError}</p>
        </div>
      )}

      {/* Status Summary */}
      <div
        className={`mb-6 p-4 rounded-lg ${
          allSuccess
            ? 'bg-green-50 border border-green-200'
            : anyError
              ? 'bg-yellow-50 border border-yellow-200'
              : 'bg-blue-50 border border-blue-200'
        }`}
      >
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              Overall Status
            </h2>
            <p className="text-sm text-gray-600 mt-1">
              {allSuccess
                ? '✅ All systems operational'
                : anyError
                  ? '⚠️ Some checks need attention'
                  : '🔄 Verifying components...'}
            </p>
          </div>
          <div className="flex space-x-4 text-sm">
            <span className="text-gray-600">
              Domain:{' '}
              <span
                className={`font-semibold ${
                  statuses.domain === 'success'
                    ? 'text-green-600'
                    : statuses.domain === 'error'
                      ? 'text-red-600'
                      : 'text-yellow-600'
                }`}
              >
                {statuses.domain === 'success'
                  ? '✓'
                  : statuses.domain === 'error'
                    ? '✗'
                    : '…'}
              </span>
            </span>
            <span className="text-gray-600">
              Auth:{' '}
              <span
                className={`font-semibold ${
                  statuses.auth === 'success'
                    ? 'text-green-600'
                    : statuses.auth === 'error'
                      ? 'text-red-600'
                      : 'text-yellow-600'
                }`}
              >
                {statuses.auth === 'success'
                  ? '✓'
                  : statuses.auth === 'error'
                    ? '✗'
                    : '…'}
              </span>
            </span>
            <span className="text-gray-600">
              Database:{' '}
              <span
                className={`font-semibold ${
                  statuses.database === 'success'
                    ? 'text-green-600'
                    : statuses.database === 'error'
                      ? 'text-red-600'
                      : 'text-yellow-600'
                }`}
              >
                {statuses.database === 'success'
                  ? '✓'
                  : statuses.database === 'error'
                    ? '✗'
                    : '…'}
              </span>
            </span>
            <span className="text-gray-600">
              Email:{' '}
              <span
                className={`font-semibold ${
                  statuses.email === 'success'
                    ? 'text-green-600'
                    : statuses.email === 'error'
                      ? 'text-red-600'
                      : 'text-yellow-600'
                }`}
              >
                {statuses.email === 'success'
                  ? '✓'
                  : statuses.email === 'error'
                    ? '✗'
                    : '…'}
              </span>
            </span>
          </div>
        </div>
      </div>

      {/* Verification Components */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Suspense fallback={<VerificationSkeleton />}>
          <DomainVerification
            onStatusChange={(status) =>
              setStatuses((prev) => ({ ...prev, domain: status }))
            }
          />
        </Suspense>
        <Suspense fallback={<VerificationSkeleton />}>
          <AuthVerification
            onStatusChange={(status) =>
              setStatuses((prev) => ({ ...prev, auth: status }))
            }
          />
        </Suspense>
        <Suspense fallback={<VerificationSkeleton />}>
          <DatabaseVerification
            onStatusChange={(status) =>
              setStatuses((prev) => ({ ...prev, database: status }))
            }
          />
        </Suspense>
        <Suspense fallback={<VerificationSkeleton />}>
          <EmailRoutingVerification
            onStatusChange={(status) =>
              setStatuses((prev) => ({ ...prev, email: status }))
            }
          />
        </Suspense>
      </div>

      {/* Next Steps */}
      {allSuccess && (
        <div className="mt-8 p-4 bg-green-50 rounded-lg border border-green-200">
          <h3 className="text-lg font-semibold text-green-900 mb-2">
            🎉 Everything is Working!
          </h3>
          <p className="text-green-800 mb-3">
            Your infrastructure is fully configured and operational. You&apos;re ready
            to start building your application.
          </p>
          <ul className="text-green-700 space-y-1 text-sm">
            <li>✅ Custom domain is properly configured</li>
            <li>✅ Authentication is working</li>
            <li>✅ Database connection is established</li>
            <li>✅ Email routing is set up</li>
          </ul>
          <p className="text-green-800 mt-4 text-sm">
            <strong>Next steps:</strong> Start building your application features.
            Check out{' '}
            <a
              href="/06_Build_Your_App.md"
              className="underline font-semibold"
              target="_blank"
            >
              the Build Your App guide
            </a>{' '}
            for examples and patterns.
          </p>
        </div>
      )}
    </div>
  );
}

