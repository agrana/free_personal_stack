export default function Home() {
  // Optional platform metadata; launching never requires a title environment variable.
  const repository = process.env.VERCEL_GIT_REPO_SLUG;
  const title = repository
    ? repository.replace(/[-_]+/g, ' ')
    : 'Your application';

  return (
    <main className='min-h-screen bg-slate-50 px-6 py-24 text-slate-900'>
      <div className='mx-auto max-w-2xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm sm:p-12'>
        <p className='text-sm font-medium uppercase tracking-widest text-slate-500'>
          {title}
        </p>
        <h1 className='mt-4 text-4xl font-semibold tracking-tight'>
          Application ready
        </h1>
        <p className='mt-6 text-lg leading-relaxed text-slate-600'>
          Your application shell is running. Build your product here when you
          are ready.
        </p>
        <p className='mt-4 leading-relaxed text-slate-600'>
          This page does not read or write application data. Deployment health
          reports the running source revision, not service configuration or data
          access readiness.
        </p>
        <a
          href='/api/health'
          className='mt-8 inline-flex rounded-lg bg-slate-900 px-5 py-3 font-medium text-white hover:bg-slate-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-slate-900'
        >
          View deployment health
        </a>
      </div>
    </main>
  );
}
