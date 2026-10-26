/**
 * Browser Supabase public key helpers.
 * Prefer NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY when nonempty; fall back to
 * NEXT_PUBLIC_SUPABASE_ANON_KEY. Public names never authorize service-role values.
 *
 * Classification mirrors app/lib/supabasePublicKey.js (build boundary).
 * JWT parsing is type screening only — not cryptographic verification.
 */

const MAX_PUBLIC_KEY_CHARS = 8192;
const PUBLISHABLE_RE = /^sb_publishable_[A-Za-z0-9_-]{16,}$/;
const PUBLIC_JWT_ROLES = new Set(['anon']);
const PRIVILEGED_JWT_ROLES = new Set(['service_role', 'supabase_admin']);

export const ERR_PUBLIC_KEY_PRIVILEGED = 'public.config.supabase_key_privileged';
export const ERR_PUBLIC_KEY_MALFORMED = 'public.config.supabase_key_malformed';
export const ERR_PUBLIC_KEY_PROJECT_MISMATCH =
  'public.config.supabase_key_project_mismatch';
export const ERR_PUBLIC_KEY_MISSING = 'public.config.supabase_key_missing';

type PublicKeyKind = 'public' | 'privileged' | 'malformed';

function b64urlJsonObject(segment: string): Record<string, unknown> | null {
  if (!segment || /[^A-Za-z0-9_-]/.test(segment)) {
    return null;
  }
  const padded = segment + '='.repeat((4 - (segment.length % 4)) % 4);
  try {
    const raw =
      typeof atob === 'function'
        ? atob(padded.replace(/-/g, '+').replace(/_/g, '/'))
        : Buffer.from(padded, 'base64url').toString('utf8');
    const parsed = JSON.parse(raw) as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch {
    return null;
  }
}

export function classifySupabasePublicKey(value: unknown): PublicKeyKind {
  if (typeof value !== 'string') {
    return 'malformed';
  }
  const text = value.trim();
  if (text.length < 16 || text.length > MAX_PUBLIC_KEY_CHARS) {
    return 'malformed';
  }
  const lower = text.toLowerCase();
  if (lower.startsWith('sb_secret_')) {
    return 'privileged';
  }
  if (PUBLISHABLE_RE.test(text)) {
    return 'public';
  }
  const parts = text.split('.');
  if (parts.length === 3 && parts[0].startsWith('eyJ')) {
    const header = b64urlJsonObject(parts[0]);
    const payload = b64urlJsonObject(parts[1]);
    if (!header || !payload) {
      return 'malformed';
    }
    if (!parts[2] || /[^A-Za-z0-9_-]/.test(parts[2])) {
      return 'malformed';
    }
    const role = payload.role;
    if (typeof role !== 'string') {
      return 'malformed';
    }
    if (PRIVILEGED_JWT_ROLES.has(role)) {
      return 'privileged';
    }
    if (PUBLIC_JWT_ROLES.has(role)) {
      return 'public';
    }
    return 'malformed';
  }
  return 'malformed';
}

export function resolvePublicSupabaseKey(env: {
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?: string;
  NEXT_PUBLIC_SUPABASE_ANON_KEY?: string;
}): string | undefined {
  const modern = (env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || '').trim();
  if (modern) {
    return modern;
  }
  const legacy = (env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '').trim();
  return legacy || undefined;
}

function projectRefFromUrl(url: string | undefined): string | null {
  if (!url) {
    return null;
  }
  const match = /^https:\/\/([a-z0-9-]+)\.supabase\.co\/?$/i.exec(url.trim());
  return match ? match[1].toLowerCase() : null;
}

function jwtRefClaim(value: string): string | null {
  const parts = value.trim().split('.');
  if (parts.length !== 3 || !parts[0].startsWith('eyJ')) {
    return null;
  }
  const payload = b64urlJsonObject(parts[1]);
  if (!payload || typeof payload.ref !== 'string' || !payload.ref.trim()) {
    return null;
  }
  return payload.ref.trim().toLowerCase();
}

export function assessJwtProjectBinding(
  env: {
    NEXT_PUBLIC_SUPABASE_URL?: string;
    SUPABASE_URL?: string;
    SUPABASE_PROJECT_ID?: string;
  },
  keyValue: string
): 'ok' | 'mismatch' | 'unknown' {
  const jwtRef = jwtRefClaim(keyValue);
  if (!jwtRef) {
    return 'unknown';
  }
  const configured =
    projectRefFromUrl(env.NEXT_PUBLIC_SUPABASE_URL) ||
    projectRefFromUrl(env.SUPABASE_URL) ||
    (env.SUPABASE_PROJECT_ID || '').trim().toLowerCase() ||
    null;
  if (!configured) {
    return 'unknown';
  }
  return configured === jwtRef ? 'ok' : 'mismatch';
}

export class PublicSupabaseKeyError extends Error {
  code: string;

  constructor(code: string) {
    super(code);
    this.name = 'PublicSupabaseKeyError';
    this.code = code;
  }
}

export function assertSafePublicSupabaseEnv(
  env: {
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?: string;
    NEXT_PUBLIC_SUPABASE_ANON_KEY?: string;
    NEXT_PUBLIC_SUPABASE_URL?: string;
    SUPABASE_URL?: string;
    SUPABASE_PROJECT_ID?: string;
  },
  options?: { requirePresent?: boolean }
): string | undefined {
  const modern = (env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY || '').trim();
  const legacy = (env.NEXT_PUBLIC_SUPABASE_ANON_KEY || '').trim();
  const candidates = [modern, legacy].filter(Boolean);
  if (!candidates.length) {
    if (options?.requirePresent) {
      throw new PublicSupabaseKeyError(ERR_PUBLIC_KEY_MISSING);
    }
    return undefined;
  }
  for (const candidate of candidates) {
    const kind = classifySupabasePublicKey(candidate);
    if (kind === 'privileged') {
      throw new PublicSupabaseKeyError(ERR_PUBLIC_KEY_PRIVILEGED);
    }
    if (kind !== 'public') {
      throw new PublicSupabaseKeyError(ERR_PUBLIC_KEY_MALFORMED);
    }
    if (assessJwtProjectBinding(env, candidate) === 'mismatch') {
      throw new PublicSupabaseKeyError(ERR_PUBLIC_KEY_PROJECT_MISMATCH);
    }
  }
  return resolvePublicSupabaseKey(env);
}
