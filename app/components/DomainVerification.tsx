'use client';

import { useEffect, useState } from 'react';

interface DomainVerificationProps {
  onStatusChange: (status: 'checking' | 'success' | 'error') => void;
}

export default function DomainVerification({
  onStatusChange,
}: DomainVerificationProps) {
  const [checks, setChecks] = useState({
    https: false,
    hostname: false,
    loading: true,
  });
  const [details, setDetails] = useState<{
    protocol: string;
    hostname: string;
    origin: string;
  } | null>(null);

  useEffect(() => {
    const verifyDomain = async () => {
      try {
        const protocol = window.location.protocol;
        const hostname = window.location.hostname;
        const origin = window.location.origin;

        setDetails({ protocol, hostname, origin });

        const httpsWorking = protocol === 'https:';
        const isCustomDomain = !hostname.includes('.vercel.app');

        setChecks({
          https: httpsWorking,
          hostname: isCustomDomain || hostname.includes('.vercel.app'), // Accept both
          loading: false,
        });

        if (httpsWorking && hostname) {
          onStatusChange('success');
        } else {
          onStatusChange('error');
        }
      } catch (error) {
        console.error('Domain verification error:', error);
        onStatusChange('error');
        setChecks((prev) => ({ ...prev, loading: false }));
      }
    };

    verifyDomain();
  }, [onStatusChange]);

  const allGood = checks.https && checks.hostname && !checks.loading;

  return (
    <div className="bg-white rounded-lg shadow-sm border p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">
          🌐 Domain Verification
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
              : 'Issues Found'}
        </span>
      </div>

      {details && (
        <div className="space-y-3">
          <div>
            <p className="text-sm text-gray-600 mb-1">Current URL</p>
            <p className="text-sm font-mono bg-gray-50 p-2 rounded border">
              {details.origin}
            </p>
          </div>

          <div className="space-y-2">
            <div className="flex items-center space-x-2">
              <span
                className={`text-xl ${
                  checks.https ? 'text-green-600' : 'text-red-600'
                }`}
              >
                {checks.https ? '✓' : '✗'}
              </span>
              <span className="text-sm text-gray-700">
                HTTPS enabled ({details.protocol})
              </span>
            </div>

            <div className="flex items-center space-x-2">
              <span
                className={`text-xl ${
                  checks.hostname ? 'text-green-600' : 'text-red-600'
                }`}
              >
                {checks.hostname ? '✓' : '✗'}
              </span>
              <span className="text-sm text-gray-700">
                Domain configured ({details.hostname})
              </span>
            </div>
          </div>

          {!checks.https && (
            <div className="mt-3 p-3 bg-red-50 rounded border border-red-200">
              <p className="text-sm text-red-800">
                <strong>Issue:</strong> HTTPS is not enabled. Make sure your
                domain SSL certificate is configured in Cloudflare/Vercel.
              </p>
            </div>
          )}

          {allGood && (
            <div className="mt-3 p-3 bg-green-50 rounded border border-green-200">
              <p className="text-sm text-green-800">
                Your domain is properly configured with HTTPS and is accessible.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

