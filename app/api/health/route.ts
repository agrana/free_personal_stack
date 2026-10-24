import { NextResponse } from 'next/server';

/**
 * App process liveness only — not database or provider readiness.
 * `status: "ready"` means this Next.js route handler is serving.
 */
export const dynamic = 'force-dynamic';
export const revalidate = 0;
export const fetchCache = 'force-no-store';

const HEALTH_SERVICE = 'app';
const FULL_SHA_RE = /^[0-9a-fA-F]{40}$/;

type RevisionResult =
  | { ok: true; revision: string }
  | { ok: false };

function resolveImmutableRevision(): RevisionResult {
  // Prefer the platform-provided immutable deploy SHA when valid.
  const vercelSha = process.env.VERCEL_GIT_COMMIT_SHA;
  if (typeof vercelSha === 'string' && FULL_SHA_RE.test(vercelSha)) {
    return { ok: true, revision: vercelSha.toLowerCase() };
  }

  // Explicit server-side local/CI test revision only — never NEXT_PUBLIC_*.
  const localTest = process.env.APP006_LOCAL_TEST_REVISION;
  if (typeof localTest === 'string' && FULL_SHA_RE.test(localTest)) {
    return { ok: true, revision: localTest.toLowerCase() };
  }

  return { ok: false };
}

function noStoreHeaders(): HeadersInit {
  return {
    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
    Pragma: 'no-cache',
  };
}

export async function GET() {
  const revision = resolveImmutableRevision();
  if (!revision.ok) {
    return NextResponse.json(
      {
        status: 'not_ready',
        revision: 'unavailable',
        service: HEALTH_SERVICE,
      },
      {
        status: 503,
        headers: noStoreHeaders(),
      }
    );
  }

  return NextResponse.json(
    {
      status: 'ready',
      revision: revision.revision,
      service: HEALTH_SERVICE,
    },
    {
      status: 200,
      headers: noStoreHeaders(),
    }
  );
}
