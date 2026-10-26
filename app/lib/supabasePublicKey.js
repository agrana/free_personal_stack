/**
 * Shared public Supabase key screening for build/config and runtime helpers.
 * JWT handling is type screening only — not cryptographic verification.
 * Errors are fixed codes; never include key material.
 */

'use strict';

const MAX_PUBLIC_KEY_CHARS = 8192;
const PUBLISHABLE_RE = /^sb_publishable_[A-Za-z0-9_-]{16,}$/;
const PUBLIC_JWT_ROLES = new Set(['anon']);
const PRIVILEGED_JWT_ROLES = new Set(['service_role', 'supabase_admin']);

const ERR_PRIVILEGED = 'public.config.supabase_key_privileged';
const ERR_MALFORMED = 'public.config.supabase_key_malformed';
const ERR_PROJECT_MISMATCH = 'public.config.supabase_key_project_mismatch';
const ERR_MISSING = 'public.config.supabase_key_missing';

function _b64urlJson(segment) {
  if (typeof segment !== 'string' || !segment || /[^A-Za-z0-9_-]/.test(segment)) {
    return null;
  }
  const padded = segment + '='.repeat((4 - (segment.length % 4)) % 4);
  try {
    const raw = Buffer.from(padded, 'base64url').toString('utf8');
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * @param {unknown} value
 * @returns {'public' | 'privileged' | 'malformed'}
 */
function classifySupabasePublicKey(value) {
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
    const header = _b64urlJson(parts[0]);
    const payload = _b64urlJson(parts[1]);
    if (!header || !payload) {
      return 'malformed';
    }
    if (typeof parts[2] !== 'string' || !parts[2] || /[^A-Za-z0-9_-]/.test(parts[2])) {
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

/**
 * Prefer modern publishable when nonempty; legacy anon only as blank fallback.
 * @param {{ NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY?: string, NEXT_PUBLIC_SUPABASE_ANON_KEY?: string }} env
 * @returns {string | undefined}
 */
function resolvePublicSupabaseKey(env) {
  if (!env || typeof env !== 'object') {
    return undefined;
  }
  const modernRaw = env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  const modern = typeof modernRaw === 'string' ? modernRaw.trim() : '';
  if (modern) {
    return modern;
  }
  const legacyRaw = env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const legacy = typeof legacyRaw === 'string' ? legacyRaw.trim() : '';
  return legacy || undefined;
}

function _projectRefFromUrl(url) {
  if (typeof url !== 'string') {
    return null;
  }
  const trimmed = url.trim();
  const match = /^https:\/\/([a-z0-9-]+)\.supabase\.co\/?$/i.exec(trimmed);
  return match ? match[1].toLowerCase() : null;
}

function _jwtRef(value) {
  if (typeof value !== 'string') {
    return null;
  }
  const parts = value.trim().split('.');
  if (parts.length !== 3 || !parts[0].startsWith('eyJ')) {
    return null;
  }
  const payload = _b64urlJson(parts[1]);
  if (!payload || typeof payload.ref !== 'string' || !payload.ref.trim()) {
    return null;
  }
  return payload.ref.trim().toLowerCase();
}

/**
 * When URL/project id and a JWT `ref` are both explicit and disagree, reject.
 * Otherwise project binding stays unknown (key strings do not prove same project).
 * @returns {'ok' | 'mismatch' | 'unknown'}
 */
function assessJwtProjectBinding(env, keyValue) {
  const jwtRef = _jwtRef(keyValue);
  if (!jwtRef) {
    return 'unknown';
  }
  const fromUrl =
    _projectRefFromUrl(env && env.NEXT_PUBLIC_SUPABASE_URL) ||
    _projectRefFromUrl(env && env.SUPABASE_URL);
  const fromId =
    env && typeof env.SUPABASE_PROJECT_ID === 'string'
      ? env.SUPABASE_PROJECT_ID.trim().toLowerCase()
      : '';
  const configured = fromUrl || (fromId || null);
  if (!configured) {
    return 'unknown';
  }
  return configured === jwtRef ? 'ok' : 'mismatch';
}

/**
 * Validate each configured public candidate (modern and legacy) even if unused.
 * Throws Error with a fixed `.code` — message equals the code (no key material).
 * @param {Record<string, string | undefined>} env
 * @param {{ requirePresent?: boolean }} [options]
 */
function assertSafePublicSupabaseEnv(env, options) {
  const opts = options || {};
  const modernRaw = env && env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY;
  const legacyRaw = env && env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  const modern = typeof modernRaw === 'string' ? modernRaw.trim() : '';
  const legacy = typeof legacyRaw === 'string' ? legacyRaw.trim() : '';

  const candidates = [];
  if (modern) {
    candidates.push(modern);
  }
  if (legacy) {
    candidates.push(legacy);
  }
  if (!candidates.length) {
    if (opts.requirePresent) {
      const err = new Error(ERR_MISSING);
      err.code = ERR_MISSING;
      throw err;
    }
    return resolvePublicSupabaseKey(env);
  }

  for (const candidate of candidates) {
    const kind = classifySupabasePublicKey(candidate);
    if (kind === 'privileged') {
      const err = new Error(ERR_PRIVILEGED);
      err.code = ERR_PRIVILEGED;
      throw err;
    }
    if (kind !== 'public') {
      const err = new Error(ERR_MALFORMED);
      err.code = ERR_MALFORMED;
      throw err;
    }
    if (assessJwtProjectBinding(env || {}, candidate) === 'mismatch') {
      const err = new Error(ERR_PROJECT_MISMATCH);
      err.code = ERR_PROJECT_MISMATCH;
      throw err;
    }
  }

  return resolvePublicSupabaseKey(env);
}

module.exports = {
  MAX_PUBLIC_KEY_CHARS,
  ERR_PRIVILEGED,
  ERR_MALFORMED,
  ERR_PROJECT_MISMATCH,
  ERR_MISSING,
  classifySupabasePublicKey,
  resolvePublicSupabaseKey,
  assessJwtProjectBinding,
  assertSafePublicSupabaseEnv,
};
