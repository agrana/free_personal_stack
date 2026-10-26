# Coding-agent guide

## Start here

This repository is a Next.js/TypeScript application and reusable infrastructure template. Read `package.json`, `docs/bootstrap.md`, relevant workflow files, and this guide before editing. Inspect `git status --short --branch` and preserve existing work. Check for more specific instructions in the directories you change.

Use GitHub's **Use this template** flow to create a new application repository. Confirm whether this checkout is the upstream template or a generated application. Reusable fixes belong upstream; product features and deployment configuration belong in the generated application. Existing generated repositories require an explicit update; template changes do not propagate automatically.

## Architecture and ownership

- Cloudflare owns DNS; Vercel hosts the application.
- Reuse one existing Supabase database with app-specific namespaces, migration ownership and access policies.
- Each application has a distinct Vercel project, hostname and infrastructure state key.
- Preserve other applications, shared database lifecycle, DNS zones and email settings. Provisioning and migration changes require explicit review.
- Legacy documentation includes earlier automation behavior. Verify it against current code and `docs/bootstrap.md` before executing it.

## Local setup and verification

Use Node.js 24.x as declared in `package.json`. Review dependency scripts before installation. Bootstrap Python dependencies in an isolated environment:

```bash
npm ci
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt
./scripts/validate-app-config.sh --config examples/app.config.example.json --mode draft
npm run lint
npm run type-check
.venv/bin/python scripts/run-all-tests.py
npm run build
```

The test runner explicitly includes hyphenated and underscored test filenames. Database integration needs Podman and a disposable PostgreSQL container; inspect `tests/shared_db_isolation_test.py` before running. Use `APP003_PG_TESTS=1` to require integration rather than silently accepting a skip. Report skips and missing prerequisites separately from passing tests. Builds may require approved public Supabase configuration; clearly label synthetic build values and keep real production secrets out of local tests.

For infrastructure edits, review provider access and downloaded dependencies before initialization:

```bash
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Initialization downloads providers. These checks establish local syntax/configuration validity, not remote permissions or a successful deployment.

## Configuration and credentials

Use `app.config.schema.json`, `examples/app.config.example.json` and the validation commands in `docs/bootstrap.md`. Ask only for missing app identity, hostname or service access. Keep non-secret configuration separate from credential values.

Use explicitly authorized credential sources and the existing preflight interface. Read credential names and presence without printing values. Keep credentials out of prompts, Git, reports, command arguments and browser bundles. GitHub secret-list access exposes names, not saved values. Project-wide Supabase service credentials bypass RLS; they do not prove app isolation. Public configuration must follow the preflight allowlist.

Use `scripts/setup-secret-placeholders.py --repo OWNER/REPO` for an authenticated
owner's dry-run inventory; after explicit target review and approval, add `--apply`
to create only missing example aliases. Never infer the target from upstream
remotes. Authenticate via `gh auth login` with a securely supplied owner PAT
(repository Secrets read/write); default Actions `GITHUB_TOKEN` cannot manage
repository secrets. Send the user to repository Settings → Secrets and variables
→ Actions to edit precreated placeholders. The sentinel
`SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` is missing, never readiness. Preserve
existing entries and avoid concurrent secret writers; see README for commands.

A valid configuration or metadata flag is not evidence of live ownership, permission or deployment authorization. Explain missing permissions with a precise next action; ask the user to authorize through a secure channel rather than paste secrets into chat.

## Bootstrap and deployment gates

`scripts/app-new.sh` provides `plan`, `apply` and `resume`; consult `--help` and `docs/bootstrap.md` for current flags. Planning generates local files and durable state, so use explicit output/state directories and preserve user edits. The initializer verifies pinned Git objects: shallow clones or generated repositories may lack required template history. Report that prerequisite and materialize the exact `PINNED_TEMPLATE_REVISION` via `bootstrap.provenance.ensure_pinned_commit_object` from the allowlisted reviewed upstream (`DEFAULT_REVIEWED_TEMPLATE_SOURCE_URL` / configured allowlisted `template` remote); do not disable provenance checks or fetch an untrusted source. See `docs/bootstrap.md` (Reviewed template source).

The shipped live-apply adapter remains blocked with `apply.live_integration_missing`. Fixture adapters exercise orchestration only. Implementing or validating a live adapter is a separate reviewed task. Do not bypass disabled apply/destroy/reset commands or re-enable unattended shared-database migrations.

Before any real mutation, verify account identity, app-scoped ownership, conflict inventory, credential permissions and shared-database preservation. Obtain explicit approval bound to the exact reviewed plan, content digest and intended changes. Revalidate on resume; preserve checkpoint bindings and reject modified generated files. Database provisioning additionally requires a reviewed compatibility/backup/recovery plan.

Use `scripts/verify-deployment.sh --help` for supported verification options. Verify the actual deployed revision and URL, health response, access controls and browser-secret exclusion. Distinguish fixture tests, local production-build tests and remote deployment evidence. Existing workflow checks or preview builds alone do not prove production readiness.

## Change delivery

Keep changes scoped, add regression tests for changed behavior, and run focused checks plus relevant full suites. Treat agent success messages as claims until independently verified. Report exact commands, results, skips, changed files and blockers.

Keep commit/push, PR creation, merge, secret binding and live provisioning as separate approval boundaries. Follow the user's authorized delivery scope. Report security findings and failed checks explicitly before recommending merge. Finish with verified artifacts and precise next actions; preserve incomplete work and explain genuine blockers.
