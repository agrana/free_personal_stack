'use client';

import { useEffect, useState } from 'react';
import { createClient } from '@/app/lib/supabase';

export default function Navigation() {
  const [session, setSession] = useState<{ user: { email?: string } } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadSession = async () => {
      try {
        const supabase = createClient();
        const {
          data: { session },
        } = await supabase.auth.getSession();
        setSession(session);
      } catch (error) {
        console.error('Error loading session:', error);
      } finally {
        setLoading(false);
      }
    };

    loadSession();

    // Listen for auth changes
    const supabase = createClient();
    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
    });

    return () => {
      subscription.unsubscribe();
    };
  }, []);

  return (
    <nav className='bg-white shadow-sm border-b'>
      <div className='max-w-7xl mx-auto px-4 sm:px-6 lg:px-8'>
        <div className='flex justify-between h-16'>
          <div className='flex items-center'>
            <h1 className='text-xl font-semibold text-gray-900'>
              Infrastructure Verification
            </h1>
          </div>
          <div className='flex items-center space-x-4'>
            {loading ? (
              <div className='w-24 h-8 bg-gray-200 animate-pulse rounded' />
            ) : session ? (
              <div className='flex items-center space-x-4'>
                <span className='text-sm text-gray-700'>
                  Welcome, {session.user.email}
                </span>
                <form action='/auth/signout' method='post'>
                  <button
                    type='submit'
                    className='bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded-md text-sm font-medium'
                  >
                    Sign Out
                  </button>
                </form>
              </div>
            ) : (
              <a
                href='/auth/signin'
                className='bg-blue-600 hover:bg-blue-700 text-white px-3 py-2 rounded-md text-sm font-medium'
              >
                Sign In
              </a>
            )}
          </div>
        </div>
      </div>
    </nav>
  );
}

