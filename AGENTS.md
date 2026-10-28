# Coding-agent guide

## Start here

Read `package.json`, `README.md`, `docs/end-to-end-onboarding.md` and relevant workflows before editing. Inspect `git status --short --branch`, preserve existing work and check directory-specific instructions. This is a Next.js/TypeScript application and reusable infrastructure template.

Use GitHub **Use this template** to generate an independent app repository, not a fork. Determine whether this checkout is upstream or a generated app. Reusable fixes belong upstream; product features belong in the generated app. Template updates do not propagate automatically.

## Required end-to-end onboarding: GitHub Actions

The required path is **generate repository → configure hostname, database and credentials in GitHub → validate connections and apply app-scoped schema migrations → deploy → configure DNS → verify the application end to end**. Operators use GitHub settings and Actions; workflows install their own tools. Before provisioning an application project, require its intended `APP_HOSTNAME`, authoritative zone and database target. Every application has an intended hostname.

Application onboarding is complete only after the intended hostname passes DNS, HTTPS, page and exact deployed-revision checks, the deployed application connects to its intended database, reviewed schema migrations are applied, and app-scoped permissions and RLS isolation are verified. A working Vercel shell is an intermediate result. See `docs/end-to-end-onboarding.md` for the required contract and actual implementation gaps.

| Operation | Required configuration | Workflow |
| --- | --- | --- |
| Launch the database-free shell at a Vercel URL | Repository secrets `VERCEL_API_TOKEN`, `VERCEL_TEAM_ID`, `SUPABASE_URL` and `SUPABASE_ANON_KEY` (modern publishable key accepted) | **Launch app** (`deploy`) |
| Complete the required application hostname and DNS | Reuse the Vercel secrets; add repository secret `CLOUDFLARE_API_TOKEN` and variables `APP_HOSTNAME`, `CLOUDFLARE_ZONE_NAME`. The token needs Zone Read and DNS Edit for the intended zone; an account-owned or user-owned API token with those permissions is supported. | **Custom domain** (`plan`, approved `apply`, `verify`) |
| Required database connection and app-scoped schema migrations | Store scoped connection and migration credentials in GitHub Secrets; define the application namespace, reviewed migrations, grants and RLS policies. Runtime access uses RLS-governed credentials. | Required live workflow remains an implementation gap; the current migration workflow is disabled. Report onboarding as incomplete. |
| Terraform administration and exceptional privileged database maintenance | Use the credentials and approvals specified by the reviewed operation. These are separate from normal app-scoped connection and migration onboarding. | Consult the advanced workflow and its safety gates |

Initial setup uses GitHub configuration rather than a local `.env` or app-source edits. Keep service-role access restricted to separately reviewed privileged operations. After editing secrets, manually dispatch the relevant workflow to validate and apply the updated configuration. See `docs/secrets-only-launch.md` and `docs/custom-domain.md` for permissions, ownership recovery and verification.

The separate CLI launcher derives project slug and immutable repository ID/SHA from authenticated GitHub metadata. Manual first deploy authorizes project creation; later default-branch pushes only update a previously owned project. Upstream numeric repository ID is blocked. A production Config marker binds project ownership. Missing markers after interruption require manually reviewed `expected_project_id` and `confirm_repository_id`; conflicting markers can never be overridden. Never infer ownership from project name alone.

The initial shell uses a lightweight landing page and liveness route. `/api/health` reports process liveness and the exact deployed SHA; a separate real application data-path check establishes database readiness. Reusable `/auth/signin` and `/auth/signout` code requires configured providers and redirects before use. Verify the required database integration, migrations and isolation even when the landing page renders successfully. Distinguish protected deployments from public verification success.

## Data and resource ownership

- Each application owns a distinct Vercel project and an intended hostname. Select the explicit intended team ID and verify the hostname routes to that owned project.
- Select the Supabase target during onboarding. Prefer a dedicated project/database for isolation; shared-project reuse is an explicit user choice with shared-resource risk. Require app-owned schema, migration ownership, least privilege, RLS and cross-user/cross-app isolation tests as normal onboarding requirements.
- The current shell launcher binds public configuration but performs no live schema migrations or application-table connection test. Its success proves that intermediate stage only. Routine application access must obey RLS; service-role credentials bypass RLS and cannot prove isolation.
- Preserve other applications, database lifecycle, unrelated DNS records and email settings during onboarding. Keep Terraform state management and privileged database operations separately scoped, with their own review and backup/recovery requirements.

## Required hostname and DNS

DNS is a required onboarding stage. Use GitHub variables `APP_HOSTNAME` and `CLOUDFLARE_ZONE_NAME`, secrets `VERCEL_API_TOKEN` / `VERCEL_TEAM_ID` / `CLOUDFLARE_API_TOKEN`, and the manual **Custom domain** workflow (`plan`, approved `apply`, `verify`). Its separate workflow is an implementation boundary within normal onboarding. See `docs/custom-domain.md`.

The domain workflow attaches the hostname to the owned Vercel project and manages only approved hostname records. Preserve deployment protection, other aliases, zone settings, Auth and database configuration. Keep deployment-specific identifiers in GitHub configuration and discovered provider state. Report the Vercel shell as an intermediate result until authoritative/public DNS, HTTPS, the application page and exact production health revision pass at `APP_HOSTNAME`. Data-backed features additionally require their own auth and RLS checks.

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
