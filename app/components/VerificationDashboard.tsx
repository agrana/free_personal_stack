'use client';

import { useEffect, useState } from 'react';
import DomainVerification from './DomainVerification';
import AuthVerification from './AuthVerification';
import DatabaseVerification from './DatabaseVerification';
import EmailRoutingVerification from './EmailRoutingVerification';
import ConfigurationCheck from './ConfigurationCheck';

interface VerificationStatus {
  domain: 'checking' | 'success' | 'error';
  auth: 'checking' | 'success' | 'error';
  database: 'checking' | 'success' | 'error';
  email: 'checking' | 'success' | 'error';
}

export default function VerificationDashboard() {
  const [statuses, setStatuses] = useState<VerificationStatus>({
    domain: 'checking',
    auth: 'checking',
    database: 'checking',
    email: 'checking',
  });
  const [configError, setConfigError] = useState<string | null>(null);

  const allSuccess = Object.values(statuses).every(
    (status) => status === 'success'
  );
  const anyError = Object.values(statuses).some((status) => status === 'error');

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
        <DomainVerification
          onStatusChange={(status) =>
            setStatuses((prev) => ({ ...prev, domain: status }))
          }
        />
        <AuthVerification
          onStatusChange={(status) =>
            setStatuses((prev) => ({ ...prev, auth: status }))
          }
        />
        <DatabaseVerification
          onStatusChange={(status) =>
            setStatuses((prev) => ({ ...prev, database: status }))
          }
        />
        <EmailRoutingVerification
          onStatusChange={(status) =>
            setStatuses((prev) => ({ ...prev, email: status }))
          }
        />
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

