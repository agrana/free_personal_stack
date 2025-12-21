'use client';

import { useEffect, useState } from 'react';

interface ConfigCheckProps {
  onError: (error: string) => void;
}

export default function ConfigurationCheck({ onError }: ConfigCheckProps) {
  const [configStatus, setConfigStatus] = useState<{
    supabaseUrl: boolean;
    supabaseKey: boolean;
    loading: boolean;
  }>({
    supabaseUrl: false,
    supabaseKey: false,
    loading: true,
  });

  useEffect(() => {
    const checkConfig = () => {
      const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
      const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

      const urlValid =
        supabaseUrl &&
        supabaseUrl.includes('supabase.co') &&
        !supabaseUrl.includes('your-') &&
        supabaseUrl !== 'your-supabase-project-url';

      const keyValid =
        supabaseKey &&
        supabaseKey.length > 20 &&
        !supabaseKey.includes('your-') &&
        supabaseKey !== 'your-supabase-anon-key';

      setConfigStatus({
        supabaseUrl: !!urlValid,
        supabaseKey: !!keyValid,
        loading: false,
      });

      if (!urlValid || !keyValid) {
        onError(
          'Supabase configuration is missing or incorrect. Check your .env.local file.'
        );
      }
    };

    checkConfig();
  }, [onError]);

  if (configStatus.loading) return null;

  if (!configStatus.supabaseUrl || !configStatus.supabaseKey) {
    return (
      <div className="mb-6 p-4 bg-red-50 rounded-lg border border-red-200">
        <h3 className="text-lg font-semibold text-red-900 mb-2">
          ⚠️ Configuration Issue Detected
        </h3>
        <div className="space-y-2 text-sm text-red-800">
          {!configStatus.supabaseUrl && (
            <p>
              ✗ <strong>NEXT_PUBLIC_SUPABASE_URL</strong> is missing or incorrect
              in .env.local
            </p>
          )}
          {!configStatus.supabaseKey && (
            <p>
              ✗ <strong>NEXT_PUBLIC_SUPABASE_ANON_KEY</strong> is missing or
              incorrect in .env.local
            </p>
          )}
        </div>
        <div className="mt-3 text-xs text-red-700">
          <p className="font-semibold mb-1">Quick fix:</p>
          <ol className="list-decimal list-inside space-y-1">
            <li>Create/update <code className="bg-red-100 px-1 rounded">.env.local</code> in the project root</li>
            <li>Add: <code className="bg-red-100 px-1 rounded">NEXT_PUBLIC_SUPABASE_URL=https://YOUR_PROJECT_ID.supabase.co</code></li>
            <li>Add: <code className="bg-red-100 px-1 rounded">NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key</code></li>
            <li>Get values from: Supabase Dashboard → Project Settings → API</li>
            <li>Restart dev server: <code className="bg-red-100 px-1 rounded">npm run dev</code></li>
          </ol>
        </div>
      </div>
    );
  }

  return null;
}

