# Managed PostgreSQL database foundation

Executable library and disposable PostgreSQL 17 tests for app-scoped
provisioning, protected migration ledgers, and runtime RLS proofs. This is the
core slice after managed-service catalog preflight; it is **not** complete
Secrets-only onboarding and does **not** claim production readiness.

## What this slice provides

| Capability | Status |
| --- | --- |
| Non-superuser `CREATEROLE` bootstrap of owned app + `<namespace>_control` | Library + PG17 tests |
| Owner `NOLOGIN` / migrator `LOGIN` / runtime `LOGIN` (no `BYPASSRLS`) | Library + PG17 tests |
| Immutable repository / project / namespace ownership binding | Library + PG17 tests |
| Allowed app role-membership graph (PG17 `ADMIN`/`SET`/`INHERIT`) | Library + PG17 tests |
| Migrator-login migration apply with checksum / order / advisory lock | Library + PG17 tests |
| Atomic DDL+ledger rollback; invariants checked **inside** the apply txn | PG17 `pg_temp` `EXECUTE` wrapper |
| Catalog ACL inspection (ordinary + partitioned parents, column ACLs) | Library + PG17 tests |
| Cross-user and cross-app RLS denial fixtures | PG17 tests |
| GitHub Secrets delivery, managed API, pooler, live workflow | **Out of scope** |
| Deployed HTTP readiness / real JWT authentication | **Separate required gates** |

Keep the older APP-003 harness (`scripts/migrate_app_namespace.py`,
`db/shared_isolation/`) and all live apply gates unchanged.

## Library entry points

Module: `scripts/managed_database.py`. Import performs no database action.

Callers pass **explicit** provisioner/migrator DSNs or open connections.
Credentials stay in memory; they are never accepted from argv for library
calls, never logged, and never placed in failure codes or object `repr`s.
Environment `DATABASE_URL` / `SUPABASE_DB_URL` are ignored.

```text
provision_app(namespace, repository_id, project_ref,
              migrator_password, runtime_password,
              provisioner_dsn=...)
migrate_app(namespace, repository_id, migrations_dir, migrator_dsn=...)
verify_post_migration_invariants(namespace, provisioner_dsn=...)
readiness_metadata(namespace, repository_id, provisioner_dsn=...)
```

### Caller-owned connections

Provided connections must be **idle and autocommit-enabled** at API entry. An
already-active transaction raises `ACTIVE_TRANSACTION` and is **not** rolled
back — unrelated uncommitted caller work is preserved and remains committable.
An idle connection with `autocommit=False` raises `CONNECTION` before any query;
its configuration remains unchanged. This prevents implicit outer transactions
from turning migration commits into savepoint releases. DSN-owned connections
are opened in autocommit by the library. Internal probe transactions (for
example invariant verification) run only after the entry checks and always
roll back their own probe work.

Trusted SQL fragments live under `db/managed_database/sql/`. Fixture apps:
`db/managed_database/fixtures/app_one` and `app_two` (same structure, distinct
bindings), plus unrelated preexisting objects under `fixtures/preexisting/`.

## Ownership and roles

- Data schema: `<namespace>` owned by `<namespace>_owner` (`NOLOGIN`).
- Control schema: `<namespace>_control` owned by the **bootstrap provisioner**,
  not owner/migrator/runtime.
- Derived roles are `NOSUPERUSER` / `NOBYPASSRLS` / `NOCREATEROLE` /
  `NOCREATEDB` / `NOREPLICATION`.
- Identifiers stay within PostgreSQL’s 63-byte limit; reserved namespaces are
  rejected.
- Allowed membership graph (only; options OR-aggregated across grantors):
  - provisioner → owner (`ADMIN` + `SET`, no `INHERIT`)
  - provisioner → migrator / runtime (`ADMIN`, no `INHERIT`, no `SET`) from
    `CREATEROLE` defaults
  - migrator → owner (`INHERIT`, `SET`, no `ADMIN`)
  - runtime / owner have empty membership closures beyond themselves
- PostgreSQL indexes memberships by `(roleid, member, grantor)`, so the
  CREATEROLE automatic `ADMIN` row and a later self-`GRANT WITH SET TRUE` can
  coexist; the library validates the effective combined options.
- Repeat provision verifies binding, role graph, and schema ACLs (including
  rejection of a missing data schema while control remains, wrong owners, and
  runtime `CREATE` on the data schema). It never resets passwords, adopts
  unmarked schemas/roles, silently repairs drift, or modifies shared/other-app
  grants. Credential rotation is explicit and outside this slice.

Bootstrap requires a NOSUPERUSER `CREATEROLE` identity with `CREATE` on the
target database. Effective provisioner identity is `CURRENT_USER`. Direct
logins must also be `NOBYPASSRLS`. Delegated CLI login may `SET ROLE` to a
bound trusted root (optional `allow_trusted_bootstrap_bypassrls`) — after
owner-schema grants the library restores that provisioner with `SET ROLE`
(not `RESET ROLE`, which would return to the ephemeral `session_user`). The
library grants that provisioner only the memberships it needs on roles it
creates, respecting PostgreSQL 17 `ADMIN`/`SET`/`INHERIT` rules. If `PUBLIC`
still has `CREATE` on schema `public`, provisioning fails closed and does not
alter shared grants.

## Migration execution contract

The migrator connects as its own LOGIN (not admin + `SET ROLE`). Each
`NNNN_name.sql` is read once; checksum and execution share the same immutable
bytes. Symlinks and path escapes are rejected. Role/schema invariants are
checked before apply and again in the atomic pre-commit gate. The
`pg_temp.md_exec_migration` and `pg_temp.md_create_login` wrappers use
`CREATE OR REPLACE` so a reused migrator or provisioner connection can replay
or provision sequentially without session-temp collisions.

Reviewed migration SQL is executed as **one** bound `EXECUTE` of the full text
inside a session `pg_temp` `SECURITY INVOKER` function (PostgreSQL parses the
script). On PostgreSQL 17, `EXECUTE` of `COMMIT` / `ROLLBACK` / `START
TRANSACTION` / `END` / `ABORT` / `SAVEPOINT` / `PREPARE TRANSACTION` raises
`FeatureNotSupported`, and procedure-mediated `COMMIT` fails with invalid
transaction termination — so DDL and ledger writes roll back together.
Statement splitting remains only for library-owned control bootstrap SQL and
unit coverage of nested comments / E-strings / dollar-quotes / quoted
identifiers. Splitting is **not** a sandbox. Cross-app authorization comes from
restricted LOGIN privileges; `record_applied` is the only protected-ledger
writer and checks `session_user` against the registered migrator (never a
caller-settable namespace GUC alone). `PUBLIC EXECUTE` on control functions is
revoked.

### Atomic post-apply invariants

Before the migration transaction commits (including replay with no new files),
catalog checks require:

- Role-membership graph and schema ACL invariants (same as provision/repeat),
  revalidated **after** migration SQL so grants such as
  `CREATE ON SCHEMA … TO PUBLIC` / `authenticated` cannot commit
- `ENABLE` + `FORCE` RLS on every ordinary and partitioned-parent table in the
  app and control schemas
- No `PUBLIC` / `anon` / `authenticated` table or column grants for
  select/write/`TRUNCATE`/references/trigger/maintain
- Runtime is non-owner / non-bypass and holds only deliberate DML table
  privileges (not `TRUNCATE` or other DDL-like grants); RLS does not protect
  `TRUNCATE`
- Runtime has no schema `CREATE`
- App-schema routines (`pg_proc`, including procedures and trigger functions):
  reject every `SECURITY DEFINER`; require owner/migrator ownership; reject
  default `PUBLIC` EXECUTE and grants to `anon` / `authenticated` / unrelated
  roles; allow only deliberate invoker `EXECUTE` to approved app roles
  (owner/migrator/runtime). Protected `<namespace>_control` bootstrap
  `SECURITY DEFINER` helpers stay valid and are not governed by this app
  baseline. Privileged app routines need a future separately reviewed
  allowlist/design — this slice does not weaken the runtime protection promise.

Schema/role drift raises `PRIVILEGE_DRIFT`. Table/column/RLS/routine violations
raise `INVARIANT_VIOLATION`. Either code rolls back newly applied DDL+ledger
atomically. The migrator inspects system catalogs and its scoped control
metadata for these checks without gaining provisioner privileges.

## Runtime RLS proof (fixtures)

Fixture `items` tables use `owner_id uuid` and policies on
`request.jwt.claim.sub`. Tests may set synthetic validated claims directly.
Authenticating real JWTs and proving deployed HTTP access remain separate
required gates. Metadata from `readiness_metadata` is non-secret
ownership/version/checksum facts only — not proof of deployed application
access.

## Failure codes and redaction

Errors raise `ManagedDatabaseError` with fixed codes such as
`NAMESPACE_INVALID`, `PUBLIC_CREATE_PRIVILEGE`, `SCHEMA_COLLISION`,
`ROLE_COLLISION`, `OWNERSHIP_MISMATCH`, `PRIVILEGE_DRIFT`,
`ACTIVE_TRANSACTION`, `MIGRATION_CHECKSUM`, `MIGRATION_ORDER`,
`MIGRATION_LOCK`, `MIGRATION_EXEC`, `INVARIANT_VIOLATION`, `AUTH_DENIED`,
`CONNECTION`. Public boundaries suppress exception cause chains (`raise ...
from None`) so standard tracebacks do not leak driver/SQL/DSN text. Messages
never include DSNs, passwords, raw driver/SQL text, or private row contents.

## Verification

```bash
APP003_PG_TESTS=1 MDB_PG_TESTS=1 \
  ../free-personal-stack-provenance/.venv/bin/python -m unittest \
  tests.managed_database_test -v
```

`APP003_PG_TESTS=1` forces this managed PG17 suite even when `MDB_PG_TESTS=0`.
A focused `MDB_PG_TESTS` override applies only without that global force.
Under global/mandatory force, a missing PG17 image or failed container start is
a hard failure, not a skip. Existing APP-003 PostgreSQL 16 fixtures stay on
`APP003_PG_TESTS` / `postgres:16-alpine`.

## Remaining integration gaps

- Credential bootstrap / pooler / Vercel runtime custody library: see
  [managed-database-delivery.md](managed-database-delivery.md). Template
  delivery config stays disabled until a reviewed activation PR. Merge enroll →
  delivery → deploy runs when enabled; deployed HTTP/JWT proof remains a
  separate reviewed gate.
- Deployed application readiness route and real JWT verification
- Enabling/relaxing existing initializer and migration apply gates (explicitly
  out of scope for the foundation and delivery library slices)
