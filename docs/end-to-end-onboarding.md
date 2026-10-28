# End-to-end application onboarding

## Required outcome

Operators configure the generated repository in GitHub and run GitHub Actions. Workflows install their tools on the runner. Every application has an intended hostname and database target before project provisioning.

Onboarding is complete only after all of these checks pass:

- The intended hostname resolves through authoritative and independent public DNS to the owned application deployment.
- HTTPS is valid, the application page loads, and the production health revision matches the approved deployment.
- The deployed application connects to its approved Supabase database using its runtime identity and can perform the intended application-table operation.
- Reviewed app-scoped schema migrations have been applied and their ledger matches the expected versions and checksums.
- Application grants are least privilege, every application table has RLS enabled with explicit policies, and cross-user and cross-app isolation checks pass using runtime credentials.

A green build, a Vercel URL, public Supabase configuration discovery or a test against disposable PostgreSQL proves an intermediate stage. Preserve separate evidence for quality CI and live end-to-end verification.

## Operator path

1. Generate an independent repository through GitHub's **Use this template**.
2. Configure GitHub Secrets for Vercel, Cloudflare and the approved database/runtime/migration identities. Configure the intended `APP_HOSTNAME`, `CLOUDFLARE_ZONE_NAME` and app-owned database namespace. Confirm the target account, zone and database before provisioning an app project.
3. Run quality checks through **App Quality (APP-006)**. Actions installs the runner dependencies and runs disposable PostgreSQL isolation tests with synthetic fixtures.
4. Run the required live database stage: verify the connection and ownership, review the app-scoped migration plan, apply the approved migrations, then check grants, RLS and the migration ledger. The required production orchestration for this stage is currently missing; the existing disabled workflow is an onboarding blocker.
5. Deploy through **Launch app** and complete hostname attachment and DNS through **Custom domain** (`plan`, approved `apply`, `verify`). These are separate implementation components of the same required onboarding process.
6. Verify the deployed application at its intended hostname, including its database operation and isolation behavior. Record the exact revision, migration versions and check results.

All operator actions, including approval gates, belong in GitHub settings, pull requests, environments and Actions. Local Node, Python, Podman, PostgreSQL and Terraform installations are optional maintainer tools, described in [maintainer verification](maintainer-verification.md).

## Current implementation and gaps

| Stage | Current evidence and implementation | Remaining requirement |
| --- | --- | --- |
| Hostname and database intake | Repository variables and public Supabase configuration are available | The shell launcher still accepts four shell-stage secrets without enforcing the full hostname/database onboarding preflight before project creation |
| Quality CI | `app-quality.yml` installs Podman on the runner and tests against disposable PostgreSQL | These tests remain fixture evidence, separate from the approved Supabase target |
| Shell deployment | `launch-app.yml` deploys the repository-owned Vercel project and checks page/health revision | Its success is an intermediate result |
| Hostname and DNS | `custom-domain.yml` provides scoped plan/apply/verify | Run and verify it for each actual application hostname |
| App-scoped schema migrations | `scripts/migrate_app_namespace.py` provides a scoped harness; `.github/workflows/deploy.yml` explicitly disables live migrations | Implement and review the protected Actions connection, bootstrap and migration orchestration before claiming full onboarding |
| Live database and isolation verification | Shell health and public configuration checks do not exercise application tables | Add protected Actions checks against the deployed application and approved database using real runtime identities |
| Combined readiness | Individual workflow results exist | Enforce all required stages in the end-to-end workflow and completion report |

This document defines the required contract and records the missing implementation. It does not enable the disabled migration workflow or claim the full path already runs.

## Database safety within normal onboarding

Connecting the database and applying app-owned migrations are normal onboarding requirements. Scope them to the approved database and namespace. Use a restricted migration identity, review migration SQL, preserve unrelated applications and shared database lifecycle, and retain migration evidence and an appropriate recovery plan.

Runtime application access obeys RLS. A service-role credential bypasses RLS and is unsuitable for isolation acceptance tests. Privileged database maintenance and Terraform administration retain separate review boundaries.
