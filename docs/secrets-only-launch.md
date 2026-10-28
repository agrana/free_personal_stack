# Secrets-only launch

## The short path

**Use this template → create a repository → fill four repository Actions secrets → Actions → Launch app → Run workflow (`deploy`).** No local `.env`, initial app-config edit or local tool installation is needed. Use the default branch, normally `main`, and leave recovery inputs empty for the first run. Editing secrets does not start a workflow; explicitly run it after setup.

Generate an independent repository rather than a fork. Choose a lowercase repository slug starting with a letter, containing only letters, digits and single separating hyphens (at most 100 characters). Leave the generated repository's **Template repository** setting off. The Vercel project name comes from this slug. Immutable GitHub repository ID establishes ownership; the current default-branch commit SHA establishes source revision. No copied app-specific project ID, title, URL or SHA needs to be edited into source.

## Accounts and permissions

- **GitHub:** permission to create the repository, manage its repository Actions secrets and manually run workflows. Organization policy must allow Actions and the workflow's referenced actions. Use a repository administrator for secret setup if your role cannot edit secrets. The runner uses the automatic `GITHUB_TOKEN` with `contents: read` for repository identity and revision discovery, not an owner PAT. It cannot manage repository secrets.
- **Vercel:** a team in which your access token can discover the team/projects, create the app project, manage its production configuration and deploy. Find the public **Team ID** in the intended team's **Settings → General** (not the team slug, display name or a personal account ID). Store the full `team_…` value as `VERCEL_TEAM_ID`. Create an access token in Vercel account settings, scoped/authorized for that same team; organization SSO or role restrictions may require an administrator. Every provider operation is explicitly scoped to the selected team; the launcher does not choose a team from an ambiguous inventory.
- **Supabase:** access to an existing project's URL and public key. You may use a project you already own or create one yourself in the provider dashboard before launch. A dedicated project/database is preferred for isolation. Reusing a shared project is supported as an explicit user choice with shared-resource risk. No database provider creation is automated.

Cloudflare, a registrar, a custom domain, service-role credentials, a Supabase management token and Terraform state credentials are unnecessary for the initial Vercel-hosted shell. Provider limits and charges remain your responsibility. After the shell is owned and production-ready, an optional GitHub-only [custom domain](custom-domain.md) path can attach one subdomain via repository variables `APP_HOSTNAME` / `CLOUDFLARE_ZONE_NAME` and a zone-scoped Cloudflare token—still without local `.env` or app-source hostname embeds.

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

Open **Actions → Launch app → Run workflow**, select the current default branch and operation `deploy`. The initial manual run authorizes creation of the slug-named project, repository marker, public production configuration and deployment of the exact checked-out SHA. Missing permissions stop the run; do not weaken ownership checks to work around them.

The workflow and launcher reject the upstream template's immutable numeric repository ID, template repositories, forks, identity mismatches, non-default-branch execution, stale SHAs and dirty source. Provider dependencies are installed before provider secrets enter the launch step. Mutations are serialized per repository and in-flight runs are not cancelled by later pushes.

Read the summary, not just the job color:

- `page_and_health_verified`: provider readback and HTTP checks matched the deployment and exact source SHA; the summary contains its verified Vercel URL.
- `ready_but_verification_protected`: Vercel reports ready, but deployment protection blocks public checks. Open the deployment through your normal authorized Vercel session. This is **not** public verification success; the workflow does not disable protection or mint a bypass token.
- `setup_required`: fill missing/placeholder secrets, then manually rerun.
- A failure status: review the scoped error and recovery handles before retrying; no unrelated project is safe to overwrite.

The server-rendered root says **Application ready**, with an optional platform-provided repository title and a generic fallback; no title environment variable is required. `/api/health` returns `status: ready`, `service: app` and the full immutable revision. It proves process liveness only, not database, authentication, email or DNS readiness. A browser review remains separate from HTTP verification.

Later default-branch pushes can deploy **only the already-owned project**. A push cannot create a project or adopt an unmarked existing project. A manual `verify` operation performs no provider writes and requires the current revision and public configuration already deployed. Rerunning an already-ready revision with unchanged public settings verifies it without another deployment. After changing public Supabase secrets, manually run `deploy`: a configuration fingerprint causes a rebuild even at the same source SHA. Saving secrets alone emits no workflow event.

## Ownership and interrupted creation recovery

The production Config marker key `TEMPLATE_REPOSITORY_ID_<numeric-repository-id>` binds the Vercel project to the immutable GitHub repository ID. Ownership uses key metadata rather than encrypted value contents; CLI Config values use API encrypted storage plus Config visibility. Name matches alone are not ownership. Existing projects without the marker are refused unless explicitly recovered. A different, malformed or conflicting marker is never overwritten, even with recovery inputs.

If creation was interrupted before the marker was saved:

1. Review the emitted `project_created_recovery_handle` / `project_readback` ID in the run summary and independently inspect the exact project's name, team and creation context in Vercel.
2. Confirm it is the intended new app, not another application's project. Obtain this generated repository's numeric ID from GitHub repository metadata; do not use its name or the upstream ID.
3. Manually run **Launch app** with operation `deploy`, `expected_project_id` set to that exact reviewed `prj_…` ID and `confirm_repository_id` set to this repository's numeric ID.
4. The runner validates exact team/name/project/repository identity before adding a missing marker. Any mismatch refuses recovery; no input authorizes overwriting another repository's marker.

Do not delete or edit ownership markers to force adoption. Repository renames can change the derived project slug; treat resulting collisions/mismatches as a review requirement, not permission to migrate or overwrite resources. There is no automatic destroy/rollback of provider resources; inspect partial progress before any separately authorized cleanup.

## Before adding application data

The root and health route do not query or mutate application tables. Reusable `/auth/signin` and `/auth/signout` endpoints and auth components remain in the repository, without root navigation links. They are reachable directly, not disabled. Review whether you want them exposed; configure Supabase auth URL/redirect allowlists, providers and session behavior before using them. A deployed shell is not evidence that sign-in works.

**Post-deployment data gate:** review app-scoped schema/table naming, migration ownership, least privilege, backup/recovery, RLS policies and cross-user/cross-app isolation tests before adding data-backed routes or UI. RLS remains **unverified** by this launch, particularly when reusing a shared database. Never treat a project-wide service-role key as proof of isolation. No migrations, schema creation, demo data operations or shared-database resets run through this launcher.

## Optional custom domain

After launch succeeds with a repository marker, see [custom-domain.md](custom-domain.md) for the manual **Custom domain** workflow (`plan` / `apply` / `verify`). It does not replace Launch app recovery for unmarked projects and never deploys source or changes Auth/database/zone settings.

## Advanced paths remain separate

Read [AGENTS.md](../AGENTS.md) for developer commands, [bootstrap.md](bootstrap.md) for the safety initializer and [app-deployment.md](app-deployment.md) for advanced guarded verification. The initializer still returns `apply.live_integration_missing`; its blocked adapter and existing disabled apply/destroy/reset/migration workflows remain blocked. Legacy initial/template/setup-trigger workflows are manual informational guides; they generate no passwords, write no issues and trigger no setup cascades. Older setup guides and secret inventories describe different workflows, not prerequisites for this shell.

Source/default-branch checks run again immediately before deployment. GitHub and Vercel remain separate services; an incoming push after that last check can briefly precede its queued update. Concurrency serializes launcher runs, and each deployment records the exact source revision and public-configuration fingerprint.
