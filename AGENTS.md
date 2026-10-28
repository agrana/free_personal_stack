# Coding-agent guide

## Start here

Read `package.json`, `README.md`, `docs/secrets-only-launch.md` and relevant workflows before editing. Inspect `git status --short --branch`, preserve existing work and check directory-specific instructions. This is a Next.js/TypeScript application and reusable infrastructure template.

Use GitHub **Use this template** to generate an independent app repository, not a fork. Determine whether this checkout is upstream or a generated app. Reusable fixes belong upstream; product features belong in the generated app. Template updates do not propagate automatically.

## Primary onboarding: GitHub-only deployment

The supported setup path is **generate repository → configure credentials and intended hostname → deploy the shell → configure DNS → verify the application at that hostname**. GitHub stores configuration and credentials; Actions executes provider operations on the default branch. A working Vercel URL proves the shell deployment stage. Application onboarding is complete only after the intended hostname passes DNS, HTTPS, page and exact deployed-revision checks.

| Operation | Required configuration | Workflow |
| --- | --- | --- |
| Launch the database-free shell at a Vercel URL | Repository secrets `VERCEL_API_TOKEN`, `VERCEL_TEAM_ID`, `SUPABASE_URL` and `SUPABASE_ANON_KEY` (modern publishable key accepted) | **Launch app** (`deploy`) |
| Complete the required application hostname and DNS | Reuse the Vercel secrets; add repository secret `CLOUDFLARE_API_TOKEN` and variables `APP_HOSTNAME`, `CLOUDFLARE_ZONE_NAME`. The token needs Zone Read and DNS Edit for the intended zone; an account-owned or user-owned API token with those permissions is supported. | **Custom domain** (`plan`, approved `apply`, `verify`) |
| Advanced Terraform or database operations | Use the credentials specified by that reviewed workflow and obtain its operation-specific approval. Database access additionally requires reviewed app-scoped grants and RLS. | Consult the advanced workflow and its safety gates |

Initial setup uses GitHub configuration rather than a local `.env` or app-source edits. Keep service-role access restricted to separately reviewed privileged operations. After editing secrets, manually dispatch the relevant workflow to validate and apply the updated configuration. See `docs/secrets-only-launch.md` and `docs/custom-domain.md` for permissions, ownership recovery and verification.

The separate CLI launcher derives project slug and immutable repository ID/SHA from authenticated GitHub metadata. Manual first deploy authorizes project creation; later default-branch pushes only update a previously owned project. Upstream numeric repository ID is blocked. A production Config marker binds project ownership. Missing markers after interruption require manually reviewed `expected_project_id` and `confirm_repository_id`; conflicting markers can never be overridden. Never infer ownership from project name alone.

Keep root layout and landing page database-free and free of inherited `Navigation` or demo verification UI. Reusable `/auth/signin` and `/auth/signout` code remains reachable without root links: configure and review auth separately. `/api/health` reports process liveness and exact deployed SHA, not database access or authentication readiness. Distinguish protected deployments from public verification success.

## Data and resource ownership

- Each application owns a distinct Vercel project and an intended hostname. Select the explicit intended team ID and verify the hostname routes to that owned project.
- Users may choose an existing Supabase project or create one externally before launch. Prefer a dedicated project/database for isolation; shared-project reuse is an explicit user choice with shared-resource risk. The launcher creates no database, schema, migrations or demo data.
- Before enabling data features, require reviewed app-scoped schema/migration ownership, least privilege, RLS and cross-user/cross-app isolation tests. Shell deployment does not verify RLS. Service-role credentials bypass RLS and cannot prove isolation.
- Preserve other applications, database lifecycle, unrelated DNS records and email settings during onboarding. Keep Terraform state management and privileged database operations separately scoped, with their own review and backup/recovery requirements.

## Required hostname and DNS

DNS is a required onboarding stage. Use GitHub variables `APP_HOSTNAME` and `CLOUDFLARE_ZONE_NAME`, secrets `VERCEL_API_TOKEN` / `VERCEL_TEAM_ID` / `CLOUDFLARE_API_TOKEN`, and the manual **Custom domain** workflow (`plan`, approved `apply`, `verify`). Its separate workflow is an implementation boundary within normal onboarding. See `docs/custom-domain.md`.

The domain workflow attaches the hostname to the owned Vercel project and manages only approved hostname records. Preserve deployment protection, other aliases, zone settings, Auth and database configuration. Keep deployment-specific identifiers in GitHub configuration and discovered provider state. Report the Vercel shell as an intermediate result until authoritative/public DNS, HTTPS, the application page and exact production health revision pass at `APP_HOSTNAME`. Data-backed features additionally require their own auth and RLS checks.

## Local developer verification

Node.js 24.x is declared in `package.json`. Review dependency scripts before installation. These developer commands are not initial launch prerequisites:

```bash
npm ci
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt
npm run lint
npm run type-check
.venv/bin/python scripts/run-all-tests.py
npm run build
```

The runner explicitly includes hyphenated and underscored test filenames. Focused shell/workflow checks:

```bash
.venv/bin/python -m unittest tests.template_shell_test tests.workflow_safety_test -v
```

Database integration requires Podman and disposable PostgreSQL; inspect `tests/shared_db_isolation_test.py`. Use `APP003_PG_TESTS=1` to require integration instead of accepting a skip. Report skips and missing prerequisites separately. Builds may require approved public Supabase configuration for retained auth routes: label synthetic local test values and keep production credentials out of local tests. The shell itself does not consume database configuration. Health tests use the explicit server-only `APP006_LOCAL_TEST_REVISION` fixture; never fake production verification.

For advanced infrastructure edits:

```bash
./scripts/validate-app-config.sh --config examples/app.config.example.json --mode draft
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Provider initialization downloads dependencies. These checks prove local validity, not live permissions or deployment.

## Credentials and optional placeholder setup

Keep non-secret configuration separate from credentials. Read names/presence without printing values. Keep secrets out of Git, prompts, reports, command arguments and browser bundles. Public Supabase configuration must pass the existing public-key allowlist. GitHub secret-list access exposes names, not values; placeholders do not establish readiness.

The UI path needs no local helper. Agents can precreate the four launch names with `scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch` (dry-run), adding `--apply` after target review and approval. The legacy default creates seven advanced bootstrap aliases. Always specify the generated `--repo OWNER/REPO`, review dry-run output and obtain approval before `--apply`. Never infer the target from upstream remotes. Authenticate via secure `gh auth login` as an authorized administrator (fine-grained repository Secrets read/write plus metadata read, or classic `repo`). The default Actions token cannot manage secrets. Existing entries must be preserved; avoid concurrent secret writers because GitHub's upsert API is not atomic create-only. `SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` means missing. Direct users to repository Settings → Secrets and variables → Actions to replace it. Never ask users to paste credentials in chat.

## Advanced initializer and safety gates

The generic launcher is separate from `scripts/app-new.sh` (`plan`, `apply`, `resume`). Read `--help` and `docs/bootstrap.md` before using the initializer. Planning writes local files/state; use explicit directories and preserve edits. It verifies pinned Git objects: generated repositories or shallow clones may lack template history. Materialize the exact pin using `bootstrap.provenance.ensure_pinned_commit_object` from the allowlisted reviewed upstream (`DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL` / reviewed `template` remote); never disable provenance checks or fetch an untrusted source.

The initializer's shipped live adapter remains blocked with `apply.live_integration_missing`; fixture adapters test orchestration only. Do not bypass disabled apply/destroy/reset commands or enable unattended shared-database migrations. A supported shell launcher does not clear those gates.

Before any advanced mutation, verify account identity, scoped ownership, conflict inventory, permissions and shared-resource preservation. Obtain approval bound to the exact reviewed plan/content digest and intended changes. Revalidate on resume and reject modified generated files. Use `scripts/verify-deployment.sh --help` for supported advanced verification. Check actual revision, URL, access controls and browser-secret exclusion; separate fixture, local-build and remote evidence.

## Change delivery

Keep changes scoped and add regression tests. Run focused checks and relevant suites; verify claims independently. Report exact commands, results, skips, changed files and blockers. Publishing, modifying secrets, provisioning, applying migrations and cleanup are separate approval boundaries. No tool success or green setup-only run proves launch readiness. Preserve incomplete work and explain genuine blockers.
