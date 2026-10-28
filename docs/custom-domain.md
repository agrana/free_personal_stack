# Required application hostname and DNS

Configure the intended application subdomain after **Launch app** has deployed the shell to its repository-owned Vercel project. DNS is a required onboarding stage. Onboarding is complete only after authoritative/public DNS, HTTPS, the application page and exact production health revision pass at this hostname. The Vercel URL verifies an intermediate shell deployment.

Operators configure GitHub repository **variables** and **secrets**, then run the manual **Custom domain** workflow. GitHub configuration and Actions provide the standard setup path; deployment-specific hostnames and provider identifiers stay outside template source.

## Prerequisites

1. Complete [secrets-only launch](secrets-only-launch.md) so the Vercel project exists with production Config marker `TEMPLATE_REPOSITORY_ID_<numeric-repository-id>`.
2. Authoritative DNS for the parent zone already lives in Cloudflare (this feature does not create or move zones).
3. Choose one hostname that is a **single label** under that zone (for example `app.example.com` under `example.com`). Apex, `www`, and `mail` are rejected.

## GitHub configuration

Repository **Actions variables** (Settings → Secrets and variables → Actions → Variables):

| Variable               | Meaning                                      |
| ---------------------- | -------------------------------------------- |
| `APP_HOSTNAME`         | Exact subdomain to attach (not a URL)        |
| `CLOUDFLARE_ZONE_NAME` | Exact authoritative zone name for that host  |

Repository **Actions secrets** (in addition to launch secrets as needed):

| Secret                 | Meaning                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `VERCEL_API_TOKEN`     | Token authorized for the explicit team and owned project                |
| `VERCEL_TEAM_ID`       | Explicit `team_…` ID (required; never inferred from names)              |
| `CLOUDFLARE_API_TOKEN` | Zone-restricted token (see permissions)                                 |

`GITHUB_TOKEN` is the standard Actions token (`contents: read` only). Secret/variable inventory names are not readiness proof.

### Cloudflare token permissions

- **Plan / verify (read):** Zone **Read** + DNS **Read**, restricted to the intended zone.
- **Apply (write):** DNS **Write** on that zone (API name; dashboard may label this Edit). Zone Read remains required to resolve the active zone ID/account. Do not grant zone settings, WAF, SSL, or account-wide DNS.

### Vercel token scope

Authorize the token for the selected team and the owned project. Domain attach/verify and read-only config/domain inventory must succeed; this workflow never creates projects or deployments.

## Operations

Actions → **Custom domain** → Run workflow on the default branch only (`workflow_dispatch`).

| Operation | Effect |
| --------- | ------ |
| `plan`    | Read-only discovery. Writes `custom-domain-plan.json` and a digest binding repository ID, automation SHA, project/team, production deployment + SHA, zone/account/hostname/NS, relevant DNS/domain routing+challenges, recommended CNAME, proposed mutations, and a **hash-only** full-zone DNS preservation baseline (raw unrelated records are never emitted). |
| `apply`   | Requires `approved_plan_digest` equal to the reviewed plan digest. Recomputes the live plan before any write. Creates only approved CNAME (DNS-only, `proxied=false`) and/or project-domain attach / TXT / verify. |
| `verify`  | Provider attachment + production routing + `verified=true`, bounded authoritative (`dig @ns +norecurse` with AA) and both public resolvers, TLS SAN via normal HTTPS trust, HTTP 200 HTML and `/api/health` matching the **production deployment SHA** (not the workflow automation SHA). |

Concurrency group `app-provider-<repository_id>` serializes with **Launch app**. It does **not** make provider APIs transactional; apply revalidates ownership, production target/SHA, recommendation, domain routing/challenges, and relevant DNS before each dependent write, then re-reads before success. Drift after prior successful writes yields `apply_partial_checkpoint` with no automatic undo.

Artifacts (runner temp, uploaded when present):

- `custom-domain-plan.json`
- `custom-domain-staged-plan.json` (TXT ownership challenge after attach)
- `custom-domain-verify.json` (including protected / failure / pending outcomes)
- `custom-domain-setup-required.json`

## Plan → apply → verify

1. Run **plan**. Review the sanitized summary/artifact. Confirm CNAME target came from authenticated Vercel `recommendedCNAME` rank `1` (exact integer `{rank,value}`; bool/fractional ranks rejected). The provider response is the trust boundary after syntactic host checks (no URL fetch / SSRF).
2. Run **apply** with `approved_plan_digest` set to that exact digest.
3. If apply returns `requires_additional_approval`, a post-attach TXT challenge was discovered. The staged **digest is the same canonical `build_plan()` digest** a fresh plan would emit for that live state. Review `custom-domain-staged-plan.json`, then run **apply** again with that staged digest (no silent replan). Challenge rotation invalidates the digest. Apex/out-of-scope challenges are refused rather than broadening write approval. Diagnostic fields such as `completed_mutations` sit outside the hashed body.
4. Run **verify**. Missing/unverified/non-production routing domains are not public success. Pending DNS/TLS is reported honestly; protected deployments emit `ready_but_verification_protected` and still persist the verify artifact. `dig` must be available on the runner.

Idempotent re-apply of an unchanged approved production configuration reports `already_configured` without duplicate objects. Branch domains, redirects, and custom environments are rejected as non-production routing.

## Ownership and recovery

Ownership is the production Config marker plus explicit team ID and derived project slug. Name-only matches are never adopted. Missing markers (including legacy projects that only set deployment metadata, or production deployments lacking `githubRepoId`) are a **plan** recovery blocker / invalid production target. Enroll the marker and redeploy through reviewed **Launch app** recovery (`expected_project_id` + `confirm_repository_id`); custom domain apply will not silently trust unmarked or legacy-metadata projects.

Upstream template numeric repository ID, forks, non-default branches, and non-dispatch events are rejected.

## Conflicts and evidence scope

- Existing conflicting A/AAAA/CNAME/TXT (and other) records for the exact hostname → plan fails closed (no overwrite/delete/adoption).
- Domains bound to another project **on the selected team** → fail closed (complete team projects+domains inventory; pagination failures fail closed).
- Collision preflight is **not** a global cross-account scan. `GET /v1/domains/{domain}/project-domains` requires a Vercel-owned apex and is **not** used for Cloudflare-hosted zones. Other Vercel accounts remain opaque; `addProjectDomain` still fail-closes on provider collision (HTTP 409) without force/move.
- Preservation baseline hashes the full zone DNS inventory in memory and emits only the hash; it does not claim live mail/apex content in artifacts. Apply only writes the approved app subdomain / in-scope TXT names.
- No source deploy, database/Auth changes, nameserver/proxy/SSL/WAF/email/zone-setting mutations, apex/www/mail edits, or paywalled features without separate approval.

## Rollback (documentation only)

Record created Cloudflare record IDs and project-domain attachment from apply output. Reverse only app-owned creates you authorized; preserve preexisting correct records, aliases, deployment protection, apex, and mail. There is no destructive rollback automation.

## Setup-required checklist

Missing or placeholder secrets/variables emit `setup_required` with names only (no secret values). Fill GitHub settings, ensure Launch app marker enrollment succeeded, then rerun **plan**.
