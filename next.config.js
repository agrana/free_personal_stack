/** @type {import('next').NextConfig} */

const {
  assertSafePublicSupabaseEnv,
  ERR_PRIVILEGED,
  ERR_MALFORMED,
  ERR_PROJECT_MISMATCH,
  ERR_MISSING,
} = require('./app/lib/supabasePublicKey');

/**
 * Prefer modern publishable when nonempty; legacy anon only as fallback.
 * Auth-helpers still consume NEXT_PUBLIC_SUPABASE_ANON_KEY — map the resolved
 * public value into that contract. Reject privileged/malformed candidates at
 * this build/config boundary before any browser inlining.
 */
const SAFE_CODES = new Set([ERR_PRIVILEGED, ERR_MALFORMED, ERR_PROJECT_MISMATCH, ERR_MISSING]);

let resolvedPublicSupabaseKey;
try {
  resolvedPublicSupabaseKey = assertSafePublicSupabaseEnv(process.env, {
    requirePresent: false,
  });
} catch (err) {
  const code = (err && err.code) || (err && err.message) || ERR_MALFORMED;
  const safe = SAFE_CODES.has(code) ? code : ERR_MALFORMED;
  const failure = new Error(safe);
  failure.code = safe;
  throw failure;
}

const modernPublishable = (process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || '').trim();

const nextConfig = {
  images: {
    domains: ['localhost'],
  },
  env: {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: resolvedPublicSupabaseKey,
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: modernPublishable || undefined,
  },
  experimental: {
    outputFileTracingIncludes: {
      '/*': ['./app/lib/certs/supabase-prod-ca-2021.crt'],
    },
    optimizePackageImports: ['@supabase/supabase-js'],
  },
  reactStrictMode: true,
};

module.exports = nextConfig;
