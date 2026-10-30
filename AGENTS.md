# Coding-agent guide

## Start here

Read `package.json`, `README.md`, `docs/end-to-end-onboarding.md` and relevant workflows before editing. Inspect `git status --short --branch`, preserve existing work and check directory-specific instructions. This is a Next.js/TypeScript application and reusable infrastructure template.

Use GitHub **Use this template** to generate an independent app repository, not a fork. Determine whether this checkout is upstream or a generated app. Reusable fixes belong upstream; product features belong in the generated app. Template updates do not propagate automatically.

## Required end-to-end onboarding: GitHub Actions

The required path is **generate repository → store Secrets in GitHub → review/merge prepared `onboarding.config.json` → automatic shell deploy and hostname/DNS → validate database connection and apply app-scoped schema migrations → verify the application end to end**. Operators fill Secrets and approve merge requests; they do not click Run workflow or copy digests for routine apply. Workflows install their own tools. Before provisioning an application project, require its intended hostname and authoritative zone in the merge-reviewed config, and the approved database target via repository Supabase Secrets (not a nonexistent onboarding.config database field). Every application has an intended hostname.

Application onboarding is complete only after the intended hostname passes DNS, HTTPS, page and exact deployed-revision checks, the deployed application connects to its intended database, reviewed schema migrations are applied, and app-scoped permissions and RLS isolation are verified. A working Vercel shell is an intermediate result. See `docs/end-to-end-onboarding.md` for the required contract and actual implementation gaps.

| Operation | Required configuration | Workflow |
| --- | --- | --- |
| Merge-driven shell deploy + required hostname/DNS | Repository secrets `VERCEL_API_TOKEN`, `VERCEL_TEAM_ID`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `CLOUDFLARE_API_TOKEN`; tracked `onboarding.config.json` with hostname, zone, and `auto_apply: true` (default `false` in the template) | **Merge onboarding** (default-branch push after App Quality succeeds) |
| Maintainer shell deploy / recovery | Same four shell secrets; optional exact `expected_project_id` + `confirm_repository_id` | **Launch app** (`workflow_dispatch`) |
| Maintainer hostname/DNS digest apply | Reuse Vercel secrets; `CLOUDFLARE_API_TOKEN`; variables `APP_HOSTNAME`, `CLOUDFLARE_ZONE_NAME` | **Custom domain** (`plan`, approved `apply`, `verify`) |
| Opt-in read-only managed-service catalog preflight | Repository secrets `SUPABASE_URL` and `SUPABASE_ACCESS_TOKEN` (Management API `database:read` / `database_read`); tracked `database-onboarding.config.json` with `enabled: true` and a safe `namespace` (template default `enabled: false`) | **Merge onboarding** after exact-head App Quality, before shell writes; see `docs/database-onboarding.md`. Catalog inspection is not database readiness. |
| Opt-in managed database delivery (existing Management + Vercel credentials) | Tracked `database-delivery.config.json` (template `enabled`/`auto_apply` false); Secrets `SUPABASE_ACCESS_TOKEN` (`database_write` + `database_pooling_config_read`), `SUPABASE_URL`, `VERCEL_API_TOKEN`, `VERCEL_TEAM_ID`. Merge path: enroll → authorized delivery → deploy; see `docs/managed-database-delivery.md`. | Template stays disabled until a reviewed config PR; deployed JWT/data-path proof remains separate. |
| Required database connection and app-scoped schema migrations | Prefer delivery library above when activated; runtime uses RLS-governed credentials in owned Vercel encrypted env. Template delivery config stays disabled. | Code path exists but template is disabled; deployed HTTP/JWT/data-path verification remains an implementation gap. The current migration workflow is disabled. Report onboarding as incomplete. |
| Terraform administration and exceptional privileged database maintenance | Use the credentials and approvals specified by the reviewed operation. These are separate from normal app-scoped connection and migration onboarding. | Consult the advanced workflow and its safety gates |

Initial setup uses GitHub Secrets plus a merge-reviewed onboarding config rather than a local `.env`. Keep service-role access restricted to separately reviewed privileged operations. Automated apply authorization is the desired-state policy approved by merge (`auto_apply`); maintainer manual-digest operations remain a separate interface. See `docs/secrets-only-launch.md` and `docs/custom-domain.md` for permissions, ownership recovery and verification.

The separate CLI launcher derives project slug and immutable repository ID/SHA from authenticated GitHub metadata. Merge-reviewed `auto_apply` with a required hostname authorizes project creation and production deployment on default-branch push; maintainer **Launch app** dispatch remains available. Upstream numeric repository ID is blocked. A production Config marker binds project ownership. Missing markers after interruption require exact `expected_project_id` and `confirm_repository_id` (config recovery fields or manual dispatch); conflicting markers can never be overridden. Never infer ownership from project name alone.

The initial shell uses a lightweight landing page and liveness route. `/api/health` reports process liveness and the exact deployed SHA; a separate real application data-path check establishes database readiness. Reusable `/auth/signin` and `/auth/signout` code requires configured providers and redirects before use. Verify the required database integration, migrations and isolation even when the landing page renders successfully. Distinguish protected deployments from public verification success.

## Data and resource ownership

- Each application owns a distinct Vercel project and an intended hostname. Select the explicit intended team ID and verify the hostname routes to that owned project.
- Select the Supabase target during onboarding. Prefer a dedicated project/database for isolation; shared-project reuse is an explicit user choice with shared-resource risk. Require app-owned schema, migration ownership, least privilege, RLS and cross-user/cross-app isolation tests as normal onboarding requirements.
- The current shell launcher binds public configuration but performs no live schema migrations or application-table connection test. Its success proves that intermediate stage only. Optional merge-time read-only catalog preflight (`docs/database-onboarding.md`) observes managed-service catalog facts only; it never authorizes migrations or claims `database_ready` / `full_app_ready`. Routine application access must obey RLS; service-role credentials bypass RLS and cannot prove isolation.
- Preserve other applications, database lifecycle, unrelated DNS records and email settings during onboarding. Keep Terraform state management and privileged database operations separately scoped, with their own review and backup/recovery requirements.

## Required hostname and DNS

DNS is a required onboarding stage. Intended hostname and Cloudflare zone come from tracked `onboarding.config.json`. With Secrets present and `auto_apply: true`, **Merge onboarding** attaches the hostname and creates only its DNS-only CNAME after App Quality succeeds on the current head. Maintainer **Custom domain** (`plan`, approved digest `apply`, `verify`) remains available and may still use repository variables `APP_HOSTNAME` / `CLOUDFLARE_ZONE_NAME`. See `docs/custom-domain.md`.

The automation attaches the hostname to the owned Vercel project and manages only approved hostname records for that configured name/zone. Preserve deployment protection, other aliases, zone settings, Auth and database configuration. Report the Vercel shell as an intermediate result until authoritative/public DNS, HTTPS, the application page and exact production health revision pass at the configured hostname. Data-backed features additionally require their own auth and RLS checks. Merge-onboarding success never means full-app-ready while database/migration/RLS orchestration remains unimplemented.

## Verification through GitHub Actions

Operators run **App Quality (APP-006)** through GitHub Actions for lint, typecheck, isolated PostgreSQL tests, build and security checks. That workflow installs Podman on its runner and provisions disposable PostgreSQL itself. Podman is a CI test-harness implementation detail, not an operator prerequisite.

Credential-free CI uses labelled synthetic public build settings and a test-only server revision fixture. These establish test/build behavior. End-to-end acceptance additionally requires the real hostname, deployed application-to-database connection, applied schema migrations and isolation checks against the approved target. Keep missing live stages visible even when quality CI passes.

Maintainers who choose to reproduce CI locally can use `docs/maintainer-verification.md`. Local Node, Python, Podman, PostgreSQL or Terraform installations are optional maintainer tooling; application onboarding runs through GitHub settings and Actions.

## Credentials and optional placeholder setup

Keep non-secret configuration separate from credentials. Read names/presence without printing values. Keep secrets out of Git, prompts, reports, command arguments and browser bundles. Public Supabase configuration must pass the existing public-key allowlist. GitHub secret-list access exposes names, not values; placeholders do not establish readiness.

The UI path needs no local helper. Agents can precreate the four launch names with `scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch` (dry-run), adding `--apply` after target review and approval. The legacy default creates seven advanced bootstrap aliases. Always specify the generated `--repo OWNER/REPO`, review dry-run output and obtain approval before `--apply`. Never infer the target from upstream remotes. Authenticate via secure `gh auth login` as an authorized administrator (fine-grained repository Secrets read/write plus metadata read, or classic `repo`). The default Actions token cannot manage secrets. Existing entries must be preserved; avoid concurrent secret writers because GitHub's upsert API is not atomic create-only. `SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` means missing. Direct users to repository Settings → Secrets and variables → Actions to replace it. Never ask users to paste credentials in chat.

## Advanced initializer and safety gates

The generic launcher is separate from `scripts/app-new.sh` (`plan`, `apply`, `resume`). Read `--help` and `docs/bootstrap.md` before using the initializer. Planning writes local files/state; use explicit directories and preserve edits. It verifies pinned Git objects: generated repositories or shallow clones may lack template history. Materialize the exact pin using `bootstrap.provenance.ensure_pinned_commit_object` from the allowlisted reviewed upstream (`DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL` / reviewed `template` remote); never disable provenance checks or fetch an untrusted source.

The initializer's shipped live adapter remains blocked with `apply.live_integration_missing`; fixture adapters test orchestration only. Do not bypass disabled apply/destroy/reset commands or enable unattended shared-database migrations. A supported shell launcher does not clear those gates.

Before any advanced mutation, verify account identity, scoped ownership, conflict inventory, permissions and shared-resource preservation. Obtain approval bound to the exact reviewed plan/content digest and intended changes. Revalidate on resume and reject modified generated files. Use `scripts/verify-deployment.sh --help` for supported advanced verification. Check actual revision, URL, access controls and browser-secret exclusion; separate fixture, local-build and remote evidence.

## Change delivery

Keep changes scoped and add regression tests. Run focused checks and relevant suites; verify claims independently. Report exact commands, results, skips, changed files and blockers. Publishing, modifying secrets, provisioning, applying migrations and cleanup are separate approval boundaries. No tool success or green setup-only run proves launch readiness. Preserve incomplete work and explain genuine blockers.
