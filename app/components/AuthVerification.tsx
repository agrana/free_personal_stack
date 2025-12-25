'use client';

import { useEffect, useState } from 'react';
import { createClient } from '@/app/lib/supabase';

interface AuthVerificationProps {
  onStatusChange: (status: 'checking' | 'success' | 'error') => void;
}

export default function AuthVerification({
  onStatusChange,
}: AuthVerificationProps) {
  const [checks, setChecks] = useState({
    session: false,
    user: false,
    loading: true,
  });
  const [userInfo, setUserInfo] = useState<{
    email?: string;
    id?: string;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const verifyAuth = async () => {
      try {
        const supabase = createClient();

        const {
          data: { session },
          error: sessionError,
        } = await supabase.auth.getSession();

        if (sessionError) {
          console.error('Session error:', sessionError);
          onStatusChange('error');
          setChecks((prev) => ({ ...prev, loading: false }));
          return;
        }

        const hasSession = !!session;
        const hasUser = !!session?.user;

        setChecks({
          session: hasSession,
          user: hasUser,
          loading: false,
        });

        if (hasUser && session.user) {
          setUserInfo({
            email: session.user.email,
            id: session.user.id,
          });
        }

        if (hasSession && hasUser) {
          onStatusChange('success');
        } else {
          onStatusChange('error');
        }
      } catch (error: any) {
        console.error('Auth verification error:', error);
        
        // Check for specific connection errors
        const errorMessage = error?.message || '';
        const isConnectionError = 
          errorMessage.includes('ENOTFOUND') || 
          errorMessage.includes('getaddrinfo') ||
          errorMessage.includes('fetch failed') ||
          errorMessage.includes('NetworkError');
        
        if (isConnectionError) {
          setError(
            'Cannot connect to Supabase. Check your NEXT_PUBLIC_SUPABASE_URL in .env.local. ' +
            'Make sure the project exists and the URL is correct.'
          );
        } else {
          setError(errorMessage || 'Authentication check failed');
        }
        
        onStatusChange('error');
        setChecks((prev) => ({ ...prev, loading: false }));
      }
    };

    verifyAuth();
  }, [onStatusChange]);

  const allGood = checks.session && checks.user && !checks.loading;

  return (
    <div className="bg-white rounded-lg shadow-sm border p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">
          🔐 Authentication Verification
        </h2>
        <span
          className={`px-3 py-1 rounded-full text-sm font-medium ${
            checks.loading
              ? 'bg-yellow-100 text-yellow-800'
              : allGood
                ? 'bg-green-100 text-green-800'
                : 'bg-red-100 text-red-800'
          }`}
        >
          {checks.loading
            ? 'Checking...'
            : allGood
              ? 'Verified'
              : 'Not Authenticated'}
        </span>
      </div>

      <div className="space-y-3">
        <div className="space-y-2">
          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.session ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.session ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">Active session</span>
          </div>

          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.user ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.user ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">User authenticated</span>
          </div>
        </div>

        {userInfo && (
          <div className="mt-3 p-3 bg-gray-50 rounded border">
            <p className="text-sm font-medium text-gray-700 mb-1">User Info</p>
            <p className="text-xs text-gray-600 font-mono">
              Email: {userInfo.email}
            </p>
            <p className="text-xs text-gray-600 font-mono">
              ID: {userInfo.id?.substring(0, 8)}...
            </p>
          </div>
        )}

        {error && (
          <div className="mt-3 p-3 bg-red-50 rounded border border-red-200">
            <p className="text-sm text-red-800 font-semibold mb-2">
              Configuration Error
            </p>
            <p className="text-sm text-red-700">{error}</p>
            <div className="mt-2 text-xs text-red-600">
              <p className="font-semibold">How to fix:</p>
              <ol className="list-decimal list-inside mt-1 space-y-1">
                <li>Check that your Supabase project exists in the dashboard</li>
                <li>Verify NEXT_PUBLIC_SUPABASE_URL in .env.local matches your project URL</li>
                <li>Ensure NEXT_PUBLIC_SUPABASE_ANON_KEY is correct</li>
                <li>Restart your dev server after changing .env.local</li>
              </ol>
            </div>
          </div>
        )}

        {!allGood && !error && (
          <div className="mt-3 p-3 bg-yellow-50 rounded border border-yellow-200">
            <p className="text-sm text-yellow-800">
              <strong>Note:</strong> You need to be signed in to verify
              authentication. This is expected if you&apos;re not logged in.
            </p>
          </div>
        )}

        {allGood && (
          <div className="mt-3 p-3 bg-green-50 rounded border border-green-200">
            <p className="text-sm text-green-800">
              Authentication is working correctly. Supabase Auth is properly
              configured.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

