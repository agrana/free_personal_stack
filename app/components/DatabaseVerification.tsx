'use client';

import { useEffect, useState } from 'react';
import { createClient } from '@/app/lib/supabase';

interface DatabaseVerificationProps {
  onStatusChange: (status: 'checking' | 'success' | 'error') => void;
}

export default function DatabaseVerification({
  onStatusChange,
}: DatabaseVerificationProps) {
  const [checks, setChecks] = useState({
    connection: false,
    write: false,
    read: false,
    cleanup: false,
    loading: true,
  });
  const [testRecordId, setTestRecordId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const verifyDatabase = async () => {
      try {
        const supabase = createClient();

        // Get current user
        const {
          data: { session },
        } = await supabase.auth.getSession();

        if (!session) {
          setError('Not authenticated - cannot test database');
          onStatusChange('error');
          setChecks((prev) => ({ ...prev, loading: false }));
          return;
        }

        // Test 1: Connection and write
        const testData = {
          message: `Database test at ${new Date().toISOString()}`,
          user_id: session.user.id,
        };

        const { data: insertData, error: insertError } = await supabase
          .from('verification_tests')
          .insert([testData])
          .select()
          .single();

        if (insertError) {
          console.error('Insert error:', insertError);
          setError(insertError.message);
          onStatusChange('error');
          setChecks((prev) => ({ ...prev, loading: false }));
          return;
        }

        setTestRecordId(insertData.id);
        setChecks((prev) => ({ ...prev, connection: true, write: true }));

        // Test 2: Read
        const { data: readData, error: readError } = await supabase
          .from('verification_tests')
          .select('*')
          .eq('id', insertData.id)
          .single();

        if (readError) {
          console.error('Read error:', readError);
          setError(readError.message);
          onStatusChange('error');
          setChecks((prev) => ({ ...prev, loading: false }));
          return;
        }

        if (readData) {
          setChecks((prev) => ({ ...prev, read: true }));
        }

        // Test 3: Cleanup (delete test record)
        if (insertData.id) {
          const { error: deleteError } = await supabase
            .from('verification_tests')
            .delete()
            .eq('id', insertData.id);

          if (!deleteError) {
            setChecks((prev) => ({ ...prev, cleanup: true }));
          }
        }

        setChecks((prev) => ({ ...prev, loading: false }));
        onStatusChange('success');
      } catch (err: any) {
        console.error('Database verification error:', err);
        
        // Check for specific connection errors
        const errorMessage = err?.message || '';
        const isConnectionError = 
          errorMessage.includes('ENOTFOUND') || 
          errorMessage.includes('getaddrinfo') ||
          errorMessage.includes('fetch failed') ||
          errorMessage.includes('NetworkError');
        
        if (isConnectionError) {
          setError(
            'Cannot connect to Supabase database. ' +
            'Your NEXT_PUBLIC_SUPABASE_URL in .env.local may be pointing to a non-existent project. ' +
            'Verify your Supabase project exists and the URL is correct.'
          );
        } else if (errorMessage.includes('relation') && errorMessage.includes('does not exist')) {
          setError(
            'Database table not found. Run migrations: npx supabase db push'
          );
        } else {
          setError(errorMessage || 'Unknown error');
        }
        
        onStatusChange('error');
        setChecks((prev) => ({ ...prev, loading: false }));
      }
    };

    verifyDatabase();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Only run once on mount

  const allGood =
    checks.connection &&
    checks.write &&
    checks.read &&
    checks.cleanup &&
    !checks.loading;

  return (
    <div className="bg-white rounded-lg shadow-sm border p-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-gray-900">
          🗄️ Database Verification
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
            ? 'Testing...'
            : allGood
              ? 'Verified'
              : 'Failed'}
        </span>
      </div>

      <div className="space-y-3">
        <div className="space-y-2">
          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.connection ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.connection ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">Connection established</span>
          </div>

          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.write ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.write ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">Write operation</span>
          </div>

          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.read ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.read ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">Read operation</span>
          </div>

          <div className="flex items-center space-x-2">
            <span
              className={`text-xl ${
                checks.cleanup ? 'text-green-600' : 'text-red-600'
              }`}
            >
              {checks.cleanup ? '✓' : '✗'}
            </span>
            <span className="text-sm text-gray-700">Delete operation</span>
          </div>
        </div>

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
                <li>Verify NEXT_PUBLIC_SUPABASE_URL in .env.local is correct</li>
                <li>If the project was deleted, create a new one or update .env.local</li>
                <li>Run migrations: <code className="bg-red-100 px-1 rounded">npx supabase db push</code></li>
                <li>Restart your dev server after changing .env.local</li>
              </ol>
            </div>
          </div>
        )}

        {allGood && (
          <div className="mt-3 p-3 bg-green-50 rounded border border-green-200">
            <p className="text-sm text-green-800">
              Database is working correctly. All CRUD operations (Create, Read,
              Delete) are functioning properly.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

