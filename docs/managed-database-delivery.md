# Managed database delivery (existing-credential integration)

Secure delivery orchestration that reuses the operator's existing Supabase
Management token, Vercel token/team, and merge-reviewed configs. This slice
wires credential bootstrap, restricted provisioning/migrations, and runtime
custody. It does **not** claim deployed HTTP/JWT data-path readiness or enable
automatic production apply in the template.

## Operator contract

1. Store existing repository Actions secrets (names only; never paste values):
   - `SUPABASE_URL`, `SUPABASE_ACCESS_TOKEN` (needs `database_write` +
     `database_pooling_config_read`)
   - `VERCEL_API_TOKEN`, `VERCEL_TEAM_ID` (owned-project encrypted env + deploy)
   - Shell/DNS secrets unchanged (`SUPABASE_ANON_KEY`, `CLOUDFLARE_API_TOKEN`)
2. Review and merge a generated-app PR that sets tracked
   `database-delivery.config.json` with `enabled` / `auto_apply` / namespace /
   migrations path / expected project ref. Template defaults keep both flags
   **false**.
3. Operators approve config merges; they do not click Run workflow or supply
   incremental tokens for the routine path.

`SUPABASE_DB_URL` / `DB_PASSWORD` are not required. Service-role keys are **not**
routine DB/RLS authority. Workflow `GITHUB_TOKEN` cannot persist repository
Secrets.

### Explicit existing-credential bootstrap (opt-in recovery path)

`bootstrap_mode` is a closed reviewed enum: `temporary_login` (default, also
when omitted by older generated repositories) or `existing_secret`. The template
remains disabled and defaults to `temporary_login`. To opt in, store the existing
credential under the fixed repository Secret **`MDB_BOOTSTRAP_DATABASE_URL`** and
merge a reviewed delivery config with `bootstrap_mode: "existing_secret"` alongside
the normal enabled/auto-apply/namespace/migrations/project binding. Never put the
value in config, a command, a PR, logs or chat. The existing Management token is
still needed for authenticated PRIMARY pooler discovery (`database_pooling_config_read`);
this mode does not need `database_write` for a CLI-login POST. Separate catalog
preflight, if enabled, retains its own permissions.

**Input contract:** libpq PostgreSQL URI (`postgresql://` / `postgres://`) or
keyword conninfo, parsed with `psycopg.conninfo.conninfo_to_dict`. Supply explicit
`host`, `port`, `dbname` (URI path), `user` and a nonempty real `password`.
The host and database must exactly match authenticated API-selected PRIMARY
metadata, port must be **5432**, and user must be **`postgres.<project_ref>`**
for the reviewed project matching `SUPABASE_URL`. Percent-encode URI credentials
or use libpq keyword quoting. Literal setup/password placeholders are rejected.
Direct `db.<ref>.supabase.co` hosts, lookalikes, another project, multi-host inputs,
arbitrary login names, and all other options (including `hostaddr`, `options`,
`service`, client certificates and service-file overrides) fail closed.

Only `sslmode` and `sslrootcert` are optional additional input keys. Accepted
`sslmode`: omitted, `require`, `verify-ca`, or `verify-full`; these are **input
normalization**, not the connection's policy. The adapter always rebuilds with
`verify-full` plus the fixed, hash-checked [reviewed provider CA](supabase-provider-trust.md).
`disable`, `allow`, `prefer`, and unknown modes are rejected. `sslrootcert` may be
omitted or the symbolic value `system`; **no caller-supplied CA file paths** are
accepted. No plaintext fixture bypass exists in this parser.

This mode **never calls the CLI login-role API**, even on missing/malformed
credentials or database rejection, and never falls back to temporary login.
Missing credentials report `setup_required`; invalid target/options/TLS fail
before any database connection or custody write. Authentication/delegation
errors expose stable safe codes, not driver exceptions. Normal native
provisioner checks, ownership, role restrictions, migrations and RLS are unchanged.
This does not resolve the uncertain outcome of any earlier CLI-login attempt;
review that separately and never delete/reset the shared role to repair it.

The bootstrap credential is used only in the in-process bootstrap connection.
It is stripped from enroll/deploy/DNS subprocess environments (therefore also
their build/Auth-verifier descendants), never persisted as runtime custody or
Vercel configuration, and excluded from reports, argv and artifacts. It has the
operator-managed lifetime of the existing credential; no new expiry is claimed.

## Existing-token capability matrix

| Provider | Capability | Used for |
| --- | --- | --- |
| Supabase Management | `database_write` | `POST /v1/projects/{ref}/cli/login-role` temporary CLI login |
| Supabase Management | `database_pooling_config_read` | `GET .../config/database/pooler` PRIMARY session host |
| Vercel | owned project env read/write | encrypted production `MDB_RUNTIME_DATABASE_URL` custody |
| Vercel | production deploy | existing shell launcher (unchanged gates) |
| GitHub | PR/config merge + Actions secret settings | reviewed desired-state policy |

The pooler response can advertise the transaction endpoint on port 6543. The adapter
keeps the authenticated API-reported PRIMARY shared-pooler host and project/database
binding, then selects its documented session endpoint on port 5432. Credentials and
password placeholders in the advertised URL are discarded. This selects a client
connection endpoint; it leaves shared project pooler configuration unchanged and
never connects on port 6543. See Supabase's [connection-mode documentation](https://supabase.com/docs/guides/database/connecting-to-postgres).

Failure artifacts may include a fixed `provider_operation` and integer
`provider_http_status` when a failing HTTP response was received; they never
include provider URLs, response bodies or credentials as diagnostics. An
attempted CLI login POST that fails or has an invalid response reports
`cli_login_mutation_possible: true`, including when no HTTP response arrived.
This is an uncertain outcome, not proof of a role change. Do not blindly retry;
review the provider state and the shared-role impact before any further action.
A previous POST with an unknown outcome remains unresolved, including earlier
generic failures whose operation is unknown. Run artifacts are ephemeral and the
local checkpoint is not a durable across-run record. This diagnostic patch alone
must not be used to automatically retry or adopt a role.

Never call `DELETE /cli/login-role` (shared CLI role deletion). Never reset the
project `postgres` password or unrelated memberships to repair login-role
failures — fail closed with a safe capability code. Provider CLI roles may be
shared across sessions; respect TTL and concurrency limits. Temporary passwords
stay in job memory only.

## Config (separate from read-only preflight)

Tracked `database-delivery.config.json` (see
`examples/database-delivery.config.example.json`):

| Field | Disabled default | When enabled |
| --- | --- | --- |
| `enabled` | `false` | `true` |
| `auto_apply` | `false` | reviewed `true` authorizes live delivery after enrollment |
| `namespace` | `null` | safe app namespace |
| `migrations_path` | `null` | in-repo relative directory (no symlinks/escapes) |
| `expected_project_ref` | `null` | must match `SUPABASE_URL` project ref |
| `rotate_migrator` | `false` | explicit job-only migrator password rotation |
| `bootstrap_mode` | `"temporary_login"` | `"temporary_login"` or explicit `"existing_secret"` (fixed extra Secret above) |

Read-only catalog preflight remains `database-onboarding.config.json`.

## Runtime vs migrator custody

- **Runtime**: durable encrypted production-only Vercel env
  `MDB_RUNTIME_DATABASE_URL` with ownership comment binding repository id,
  project ref, namespace, and owned Vercel project id. Inventory + binding
  checks precede writes. Persist the secret **before** creating the DB login.
  Never reset an existing runtime password on repeat. Unknown/unreadable keys
  → `recovery_required` (not automatic reset). Probe recovered LOGIN before
  reuse.
- **Migrator**: job-only. First provision generates it; later reviewed
  deliveries may set `rotate_migrator: true` after ownership/grants/source
  binding verify. Password changes use bound temp invoker helpers (no secret
  interpolation into SQL source). Bounded Supavisor auth-cache retries only.
- Never store migrator/admin/Management credentials in Vercel app env. No
  `NEXT_PUBLIC_*` DB secrets. No CLI passwords/DSNs in subprocess argv.

## Provisioner identity

Effective privileged identity is `CURRENT_USER` after optional `SET ROLE`
delegation from the temporary CLI login to the bound trusted bootstrap root
(`postgres` on managed Supabase). That root may hold `BYPASSRLS`. Direct
non-elevated bootstrap (NOSUPERUSER + CREATEROLE + NOBYPASSRLS) remains
supported and covered by PG17 tests. App owner/migrator/runtime roles stay
`NOSUPERUSER` / `NOBYPASSRLS` / `NOCREATEROLE`. Ledger authorization continues
to use migrator `session_user`.

TLS: real Supabase connections use `sslmode=verify-full` with an explicit
reviewed provider CA (`sslrootcert`); missing/tampered trust fails closed. Plaintext is allowed only for
explicit loopback disposable fixtures and is never a production bypass.
Migrator credentials are job-memory only in this slice (no durable expiry API
is claimed).

## Merge seam (owned enrollment → delivery → deploy)

Merge onboarding loads the delivery decision **after** exact-head quality and
read-only preflight, **before** production deploy:

- `enabled: false` → `delivery_disabled` (no network, normal shell/DNS)
- `enabled: true`, `auto_apply: false` → `delivery_auto_apply_disabled`
- `enabled: true`, `auto_apply: true` → launch `--operation enroll` (owned
  project + marker, no deploy), then `execute_authorized_delivery` against that
  binding, then normal production deploy + existing hostname/DNS flow

`execute_authorized_delivery` verifies GitHub repository/source SHA identity,
authoritative exact-head quality evidence, and GET owned Vercel
project/team/name/marker before custody or CLI login. Production entry does
not accept pooler/login/provisioner overrides; fixtures use
`execute_delivery(..., allow_test_orchestration=True)`.

Deployment reuse additionally binds non-secret
`templateDeliveryBindingSha256` when delivery ran. A READY public-config-only
shell for the same SHA is not reused as a database-delivered deployment.

Template `database-delivery.config.json` stays `enabled`/`auto_apply` false.
Initializer/live migration npm gates stay disabled. `database_ready` /
`full_app_ready` remain false until deployed HTTP/JWT/data-path evidence
exists (separate gate).

Executable library entry points:

- `scripts/database-delivery-config.py`
- `scripts/supabase_management_database.py`
- `scripts/vercel_env_custody.py`
- `scripts/managed_database_delivery.py`
- `scripts/managed_database.py` (provision/migrate/password helper)
- `scripts/launch-app.py` (`deploy` | `verify` | `enroll`)

## Verification

Existing-secret regression coverage executes provisioning and migrations against
real disposable PostgreSQL 17 with a `NOSUPERUSER CREATEROLE NOBYPASSRLS` bootstrap
login. A fixture connector maps the validated provider host/login to loopback
PostgreSQL; mocked Management/custody transports make no provider requests. Tests
also execute real authentication/delegation failures, runtime own-user CRUD,
cross-user/DDL denials, secret canary scans, and child-environment exclusion.
These tests prove SQL/validation boundaries, not live Supavisor authentication,
provider TLS negotiation, or deployed application readiness.

```bash
APP003_PG_TESTS=1 MDB_PG_TESTS=1 MDB_DELIVERY_PG_TESTS=1 \
  ../free-personal-stack-provenance/.venv/bin/python -m unittest \
  tests.managed_database_delivery_test tests.managed_database_test \
  tests.merge_onboarding_test -v
```
