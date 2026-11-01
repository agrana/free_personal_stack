# Secrets-only launch

## The short path

This guide covers the shell-deployment stage of application onboarding. Configure the intended hostname/zone in `onboarding.config.json` and the approved database target via Supabase Secrets before project provisioning. Follow the [full onboarding contract](end-to-end-onboarding.md), including required database connection, schema migrations and [hostname/DNS setup](custom-domain.md). Onboarding is complete only after DNS, HTTPS, the page and exact deployed-revision checks pass at the intended hostname, runtime database access works, migrations are applied, and grants and RLS isolation are verified. The current live migration orchestration remains an implementation gap; a working Vercel URL is an intermediate result.

**Use this template → create a repository → fill repository Actions secrets (four shell secrets plus `CLOUDFLARE_API_TOKEN` for DNS) → review/merge a prepared `onboarding.config.json` PR with hostname, zone, and `auto_apply: true`.** Default-branch push then runs **Merge onboarding** after App Quality succeeds. No local `.env` or Run workflow click is required for routine shell+DNS apply. Maintainer **Launch app** (`workflow_dispatch`) remains available for recovery and exceptional deploy.

Generate an independent repository rather than a fork. Choose a lowercase repository slug starting with a letter, containing only letters, digits and single separating hyphens (at most 100 characters). Leave the generated repository's **Template repository** setting off. The Vercel project name comes from this slug. Immutable GitHub repository ID establishes ownership; the current default-branch commit SHA establishes source revision. No copied app-specific project ID, title, URL or SHA needs to be edited into source.

## Accounts and permissions

- **GitHub:** permission to create the repository, manage its repository Actions secrets, and merge prepared onboarding-config PRs. Organization policy must allow Actions and the workflow's referenced actions. Use a repository administrator for secret setup if your role cannot edit secrets. The runner uses the automatic `GITHUB_TOKEN` with `contents: read` (and `actions: read` on Merge onboarding) for identity, revision and quality-gate discovery, not an owner PAT. It cannot manage repository secrets.
- **Vercel:** a team in which your access token can discover the team/projects, create the app project, manage its production configuration and deploy. Find the public **Team ID** in the intended team's **Settings → General** (not the team slug, display name or a personal account ID). Store the full `team_…` value as `VERCEL_TEAM_ID`. Create an access token in Vercel account settings, scoped/authorized for that same team; organization SSO or role restrictions may require an administrator. Every provider operation is explicitly scoped to the selected team; the launcher does not choose a team from an ambiguous inventory.
- **Supabase:** access to an existing project's URL and public key. You may use a project you already own or create one yourself in the provider dashboard before launch. A dedicated project/database is preferred for isolation. Reusing a shared project is supported as an explicit user choice with shared-resource risk. No database provider creation is automated.

The four shell secrets below plus `CLOUDFLARE_API_TOKEN` configure merge-driven shell deploy and hostname/DNS. Optional read-only database catalog preflight additionally needs `SUPABASE_ACCESS_TOKEN` and a merge-reviewed `database-onboarding.config.json` ([database onboarding](database-onboarding.md)); it never completes database readiness. Intended hostname and zone come from tracked `onboarding.config.json`, not mandatory GitHub variable steps. Account-owned and user-owned Cloudflare API tokens are supported with Zone Read and DNS Edit permissions for the intended zone. Maintainer [Custom domain](custom-domain.md) digest apply may still use repository variables. Advanced Terraform and privileged database operations have their own reviewed credential and approval requirements. Provider limits and charges remain your responsibility.

## Add the four secrets

In the generated repository, use **Settings → Secrets and variables → Actions → Repository secrets → New repository secret**. These are repository secrets, not environment secrets or Actions variables.

| Name                | Value and boundary                                                                                                  |
| ------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `VERCEL_API_TOKEN`  | Management access token authorized for the selected Vercel team; never published to the browser                     |
| `VERCEL_TEAM_ID`    | Explicit public team ID (`team_…`), stored as a secret for setup convenience                                        |
| `SUPABASE_URL`      | The project's HTTPS URL from Supabase project settings / Connect                                                    |
| `SUPABASE_ANON_KEY` | Prefer the modern `sb_publishable_…` key from project API key settings; valid legacy anon JWT keys remain supported |

The alias `SUPABASE_ANON_KEY` accepts a modern publishable key despite its historical name. Never supply `sb_secret_…`, a `service_role` JWT, or another privileged key. Public Supabase URL/key configuration is intentionally browser-safe, **not authorization or isolation**. The launcher validates public-key classification and performs bounded read-only discovery; it does not prove database RLS or authentication readiness. It binds the URL/key to the app's production public configuration. Vercel credentials stay in the runner/CLI boundary.

Blank values and `SETUP_REQUIRED_REPLACE_WITH_REAL_VALUE` produce `setup_required`, not a deployment. A green setup-required run is not launch success. Secret inventory exposes names only, never saved values.

### Optional secret-name placeholders

Create the four names directly in GitHub, or let an authorized agent precreate them so you can edit them with the pencil icons. The optional helper defaults to a dry-run and preserves existing names:

```sh
python3 scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch
# After reviewing the target and approving placeholder creation:
python3 scripts/setup-secret-placeholders.py --repo '<username>/<repository>' --profile launch --apply
```

The legacy default creates seven advanced bootstrap aliases; `--profile launch` selects exactly the four launch names. No placeholder is a credential.

Authenticate `gh` securely as the explicitly authorized repository owner/administrator. An optional fine-grained PAT needs repository **Secrets: read and write** and metadata read; a classic PAT needs `repo` scope. The automatic Actions token cannot gain secret-management rights by changing workflow permissions. Environment `GH_TOKEN` / `GITHUB_TOKEN` overrides saved CLI login. Never put token values in commands, chat or tracked files. Select the generated target explicitly as `<username>/<repository>`; do not infer it from an upstream remote. After target review and explicit approval, a helper may create only missing placeholders; preserve existing names, avoid concurrent secret writers (GitHub's API is an upsert), and replace placeholders in the GitHub UI.

## Run and verify

Routine path: merge `onboarding.config.json` with `auto_apply: true` and a required hostname/zone after Secrets are filled. **Merge onboarding** waits for successful current-head **App Quality**, then deploys the shell and applies hostname/DNS. Merge of that desired-state policy is the apply authorization. Missing permissions stop the run; do not weaken ownership checks to work around them.

Maintainer path: **Actions → Launch app → Run workflow** on the default branch (`deploy`) still authorizes creation/recovery with optional exact project/repository IDs when automation is disabled or interrupted.

The workflows and launchers reject the upstream template's immutable numeric repository ID, template repositories, forks, identity mismatches, non-default-branch execution, stale SHAs and dirty source. Provider dependencies are installed before provider secrets enter write steps. Mutations share concurrency group `app-provider-<repository_id>`.

Read the summary, not just the job color:

- `infrastructure_dns_verified_database_incomplete`: shell+hostname/DNS verified; database connection, migrations and RLS remain unimplemented — not full-app-ready.
- `page_and_health_verified` (Launch app): provider readback and HTTP checks matched the deployment and exact source SHA.
- `ready_but_verification_protected`: Vercel reports ready, but deployment protection blocks public checks. This is **not** public verification success.
- `setup_required` / `onboarding_auto_apply_disabled`: fill Secrets or merge `auto_apply` policy; no provider writes.
- A failure status: review the scoped error and recovery handles before retrying; no unrelated project is safe to overwrite.

The server-rendered root says **Application ready**, with an optional platform-provided repository title and a generic fallback; no title environment variable is required. `/api/health` returns `status: ready`, `service: app` and the full immutable revision. It proves process liveness only, not database, authentication, email or DNS readiness. A browser review remains separate from HTTP verification.

With `auto_apply: false` (template default), merge onboarding reports disabled and writes nothing. An unchanged rerun is idempotent. Saving secrets alone does not authorize apply until the reviewed config is merged.

## Ownership and interrupted creation recovery

The production Config marker key `TEMPLATE_REPOSITORY_ID_<numeric-repository-id>` binds the Vercel project to the immutable GitHub repository ID. Ownership uses key metadata rather than encrypted value contents; CLI Config values use API encrypted storage plus Config visibility. Name matches alone are not ownership. Existing projects without the marker are refused unless explicitly recovered. A different, malformed or conflicting marker is never overwritten, even with recovery inputs.

If creation was interrupted before the marker was saved:

1. Review the emitted `project_created_recovery_handle` / `project_readback` ID in the run summary and independently inspect the exact project's name, team and creation context in Vercel.
2. Confirm it is the intended new app, not another application's project. Obtain this generated repository's numeric ID from GitHub repository metadata; do not use its name or the upstream ID.
3. Recover via maintainer **Launch app** dispatch with exact `expected_project_id` and `confirm_repository_id`, or merge that same pair into the corresponding `onboarding.config.json` recovery fields with `auto_apply: true` for the next Merge onboarding run.
4. The runner validates exact team/name/project/repository identity before adding a missing marker. Any mismatch refuses recovery; no input authorizes overwriting another repository's marker.

Do not delete or edit ownership markers to force adoption. Repository renames can change the derived project slug; treat resulting collisions/mismatches as a review requirement, not permission to migrate or overwrite resources. There is no automatic destroy/rollback of provider resources; inspect partial progress before any separately authorized cleanup.

## Before adding application data

The root and health route do not query or mutate application tables. Reusable `/auth/signin` and `/auth/signout` endpoints and auth components remain in the repository, without root navigation links. They are reachable directly, not disabled. Review whether you want them exposed; configure Supabase auth URL/redirect allowlists, providers and session behavior before using them. A deployed shell is not evidence that sign-in works.

**Post-deployment data gate:** review app-scoped schema/table naming, migration ownership, least privilege, backup/recovery, RLS policies and cross-user/cross-app isolation tests before adding data-backed routes or UI. RLS remains **unverified** by this launch, particularly when reusing a shared database. Never treat a project-wide service-role key as proof of isolation. No migrations, schema creation, demo data operations or shared-database resets run through this launcher.

## Required hostname and DNS

Hostname and zone belong in `onboarding.config.json`. With `auto_apply: true`, **Merge onboarding** performs attachment and DNS after shell deploy. Maintainer [custom-domain.md](custom-domain.md) digest apply remains available. Complete recovery first for an unmarked project. Automation manages only the configured hostname's attachment and DNS-only CNAME while preserving source deployments, Auth, databases and zone settings.

## Advanced paths remain separate

Read [AGENTS.md](../AGENTS.md) for developer commands, [bootstrap.md](bootstrap.md) for the safety initializer and [app-deployment.md](app-deployment.md) for advanced guarded verification. The initializer still returns `apply.live_integration_missing`; its blocked adapter and existing disabled apply/destroy/reset/migration workflows remain blocked. Legacy initial/template/setup-trigger workflows are manual informational guides; they generate no passwords, write no issues and trigger no setup cascades. Older setup guides and secret inventories describe different workflows, not prerequisites for this shell.

Source/default-branch checks run again immediately before deployment. GitHub and Vercel remain separate services; an incoming push after that last check can briefly precede its queued update. Concurrency serializes launcher runs, and each deployment records the exact source revision and public-configuration fingerprint.
