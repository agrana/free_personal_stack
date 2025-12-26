import './globals.css';
import { Inter } from 'next/font/google';
import Navigation from '@/app/components/Navigation';

const inter = Inter({ subsets: ['latin'] });

export const metadata = {
  title: 'Infrastructure Verification',
  description: 'Verify that your infrastructure components are properly configured',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang='en'>
      <body className={inter.className}>
        <Navigation />
        <main className='min-h-screen bg-gray-50'>{children}</main>
      </body>
    </html>
  );
}
