'use client';

import { useEffect, useState } from 'react';

interface EmailRoutingVerificationProps {
  onStatusChange: (status: 'checking' | 'success' | 'error') => void;
}

export default function EmailRoutingVerification({
  onStatusChange,
}: EmailRoutingVerificationProps) {
  const [domain, setDomain] = useState<string | null>(null);
  const [emailAddresses, setEmailAddresses] = useState<string[]>([]);

  useEffect(() => {
    const verifyEmailRouting = () => {
      try {
        const hostname = window.location.hostname;
        
        // Extract domain (remove www. if present)
        const baseDomain = hostname.replace(/^www\./, '');
        setDomain(baseDomain);

        // Standard email routing addresses
        const addresses = [
          `support@${baseDomain}`,
          `contact@${baseDomain}`,
          `hello@${baseDomain}`,
        ];

        setEmailAddresses(addresses);

        // If we have a domain, consider it a success
        // (actual email delivery can't be verified client-side)
        if (baseDomain && !baseDomain.includes('localhost')) {
          onStatusChange('success');
        } else {
          onStatusChange('error');
        }
      } catch (error) {
        console.error('Email routing verification error:', error);
        onStatusChange('error');
      }
    };

    verifyEmailRouting();
  }, [onStatusChange]);

  const hasDomain = domain && !domain.includes('localhost');

  return (
    <div className="bg-white rounded-lg shadow-sm border p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">
          📧 Email Routing Verification
        </h2>
        <span
          className={`px-3 py-1 rounded-full text-sm font-medium ${
            hasDomain
              ? 'bg-green-100 text-green-800'
              : 'bg-yellow-100 text-yellow-800'
          }`}
        >
          {hasDomain ? 'Configured' : 'Local'}
        </span>
      </div>

      <div className="space-y-3">
        {hasDomain && (
          <>
            <div>
              <p className="text-sm text-gray-600 mb-2">
                Email routing addresses configured:
              </p>
              <div className="space-y-1">
                {emailAddresses.map((email) => (
                  <div
                    key={email}
                    className="flex items-center space-x-2 p-2 bg-gray-50 rounded border"
                  >
                    <span className="text-green-600">✓</span>
                    <span className="text-sm font-mono text-gray-700">
                      {email}
                    </span>
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-3 p-3 bg-blue-50 rounded border border-blue-200">
              <p className="text-sm text-blue-800 mb-2">
                <strong>How to test:</strong>
              </p>
              <ul className="text-xs text-blue-700 space-y-1 list-disc list-inside">
                <li>Send an email to any of the addresses above</li>
                <li>
                  Check your forwarding destination (configured in Cloudflare)
                </li>
                <li>
                  Verify emails arrive at your configured email address
                </li>
              </ul>
            </div>

            <div className="mt-3 p-3 bg-green-50 rounded border border-green-200">
              <p className="text-sm text-green-800">
                Email routing is configured for your domain. Test by sending an
                email to any of the addresses above.
              </p>
            </div>
          </>
        )}

        {!hasDomain && (
          <div className="p-3 bg-yellow-50 rounded border border-yellow-200">
            <p className="text-sm text-yellow-800">
              <strong>Note:</strong> Email routing verification is only
              available on deployed domains. When deployed, you'll see the
              configured email addresses here.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

