# Reviewed Supabase provider trust

`app/lib/certs/supabase-prod-ca-2021.crt` is a public trust anchor, not a
credential. It is scoped to validated Supabase session-pooler connections,
not installed globally and not used for Management API HTTPS. Production
Python DSNs retain `sslmode=verify-full`; Node retains
`rejectUnauthorized: true` and its standard hostname verifier. Both explicitly
select this same provider root, rather than relying on different ambient CA
sets. There is no `require` fallback, runtime download, arbitrary CA-path input,
or environment-variable trust override.

## Reviewed provenance and immutable pins

The official [psql guide](https://supabase.com/docs/guides/database/psql)
documents Session pooler + `verify-full` and the certificate downloaded from
Database Settings. The reviewed official dashboard source is pinned to
Supabase commit `a05e7ec1d79e67cba65e3871b7caf105db27f8b1`:

- [SSLConfiguration.tsx](https://github.com/supabase/supabase/blob/a05e7ec1d79e67cba65e3871b7caf105db27f8b1/apps/studio/components/interfaces/Settings/Database/SSLConfiguration.tsx)
  selects custom content `ssl:certificate_url` with `env: prod`.
- [custom-content.json](https://github.com/supabase/supabase/blob/a05e7ec1d79e67cba65e3871b7caf105db27f8b1/apps/studio/hooks/custom-content/custom-content.json)
  defines `https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/${env}/ssl/${env}-ca-2021.crt`.
- Exact resolved HTTPS source (retrieved without redirects during provenance
  review): `https://supabase-downloads.s3-ap-southeast-1.amazonaws.com/prod/ssl/prod-ca-2021.crt`.
  The URL is mutable; the shipped bytes and the immutable source-code pin above
  are the review boundary, not future contents of that URL.
- PEM size: **1367 bytes**.
- PEM SHA-256: `700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7`.
- DER SHA-256 fingerprint: `80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA`.
- Validity: 2021-04-28 through 2031-04-26 (UTC); self-signed CA, `CA:TRUE`,
  certificate-signing and CRL-signing usage. Tests parse the actual shipped
  certificate and verify its signature with OpenSSL.

Both loaders hash the complete public file on every use and fail closed on
missing/unreadable or mismatched bytes. Rotation requires another reviewed
asset + code-pin change, including hostname/chain tests, before expiry; neither
loader silently renews trust. Next.js explicitly traces the fixed asset into
server deployment bundles. It must not be imported into browser code.

## End-to-end consumers and custody

The shared Python builder covers provisioner, restricted migrator and runtime
DSNs. Existing-bootstrap parsing still rejects arbitrary root paths and client
certificate/options overrides. An input symbolic `system` value is accepted
only as legacy input syntax: the parser rebuilds with the reviewed provider CA,
never uses that selector verbatim. Loopback plaintext fixtures retain their
separate closed boundary; production cannot select a fixture CA.

Custody stores the complete runtime conninfo, including the producer's absolute
root path. Python custody recovery requires the exact expected path and rebuilds
the DSN; it does not trust arbitrary persisted paths. Previously stored
system-root conninfo (or a path from a different checkout) is not silently
rewritten or rotated: it fails closed and needs separately reviewed custody
recovery. This change authorizes no credential or provider mutation.

Generated Node adopters must use `supabaseProviderSslOptions()` after validating
their provider target, rather than reading a runner path or trusting Node's
native roots alone. The helper resolves only the fixed packaged asset. A
caller-supplied conninfo path never chooses Node trust. The reusable template's
initial shell remains database-free; Arena wires this helper into its notes
consumer separately.

## Evidence boundaries and chain selection

Provenance, an internally valid CA, and successful local TLS tests do **not**
prove any particular live endpoint presents a chain rooted here. Only an
independently authorized live connection can establish that. This policy uses
only the reviewed provider root for the scoped pooler, consistently in Python
and Node; an endpoint presenting an unrelated/public-CA chain fails closed,
not by silently widening trust to system/native roots. Review actual endpoint
chain evidence before broadening or replacing this policy.

The regression exercises installed psycopg/libpq and Node TLS against owned
loopback SSLRequest listeners: test-only trusted fixture root succeeds through
TLS/startup; system roots and the unrelated shipped provider root reject it;
the trusted root with a mismatched hostname also rejects. The fixture root is
injected directly into test clients only, never through a production override.

Client `verify-full`/Node hostname verification establishes the client-to-pooler
TLS boundary when a connection succeeds. `pg_stat_ssl` observed through the
pooler describes the PostgreSQL backend leg; it cannot independently prove the
client-to-pooler certificate or hostname checks. Neither observation alone is
application readiness, migration authorization, or an end-to-end data-path proof.
