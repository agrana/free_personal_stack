import './globals.css';
import { Inter } from 'next/font/google';
import { createServerClient } from '@/app/lib/supabase-server';
import { redirect } from 'next/navigation';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Personal Project Template',
  description: 'A template for quickly starting new personal projects',
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = createServerClient();

  const {
    data: { session },
  } = await supabase.auth.getSession();

  return (
    <html lang='en'>
      <body className={inter.className}>
        <nav className='bg-white shadow-sm border-b'>
          <div className='max-w-7xl mx-auto px-4 sm:px-6 lg:px-8'>
            <div className='flex justify-between h-16'>
              <div className='flex items-center'>
                <h1 className='text-xl font-semibold text-gray-900'>
                  Personal Project Template
                </h1>
              </div>
              <div className='flex items-center space-x-4'>
                {session ? (
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
        <main className='min-h-screen bg-gray-50'>{children}</main>
      </body>
    </html>
  );
}
