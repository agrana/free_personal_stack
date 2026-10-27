import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Application ready',
  description: 'A minimal application shell, ready for your next product.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang='en'>
      <body className='bg-slate-50 font-sans antialiased'>{children}</body>
    </html>
  );
}
