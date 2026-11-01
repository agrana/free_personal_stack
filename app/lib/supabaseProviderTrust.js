'use strict';

const fs = require('fs');
const path = require('path');
const { createHash } = require('crypto');

const PROVIDER_CA_SHA256 =
  '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7';

/** Server-only, fixed public trust asset. No env/caller CA paths or downloads. */
function supabaseProviderSslOptions() {
  let ca;
  try {
    ca = fs.readFileSync(
      path.join(process.cwd(), 'app/lib/certs/supabase-prod-ca-2021.crt'),
    );
  } catch (_) {
    throw new Error('provider_ca_unavailable');
  }
  if (createHash('sha256').update(ca).digest('hex') !== PROVIDER_CA_SHA256) {
    throw new Error('provider_ca_integrity_failed');
  }
  // Deliberately replace, not silently append ambient/native roots. Both Node
  // and libpq use exactly the reviewed provider root for this provider scope.
  // Node's normal hostname verification remains enabled (no custom callback).
  return { rejectUnauthorized: true, ca: ca.toString('ascii') };
}

module.exports = { supabaseProviderSslOptions };
