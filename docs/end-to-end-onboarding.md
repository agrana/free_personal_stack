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
2. Configure GitHub Secrets for Vercel, Cloudflare and the approved database/runtime/migration identities (the live database target comes from those Supabase Secrets, not from an `onboarding.config.json` database field). Merge a prepared `onboarding.config.json` with the intended hostname, Cloudflare zone and `auto_apply` policy. Confirm the target account, zone and database before provisioning an app project. App-owned schema/namespace, migrations, grants and RLS remain mandatory readiness requirements; live orchestration for them is still missing.
3. **App Quality (APP-006)** runs on the default-branch head. Actions installs the runner dependencies and runs disposable PostgreSQL isolation tests with synthetic fixtures. **Merge onboarding** gates provider writes on that successful current-head quality run.
4. Run the required live database stage: verify the connection and ownership, review the app-scoped migration plan, apply the approved migrations, then check grants, RLS and the migration ledger. The required production orchestration for this stage is currently missing; the existing disabled workflow is an onboarding blocker.
5. **Merge onboarding** deploys the shell and applies hostname/DNS under the merge-reviewed policy. Maintainer **Launch app** / **Custom domain** digest workflows remain available. Shell+DNS success is reported separately from missing database stages and never as full-app-ready.
6. Verify the deployed application at its intended hostname, including its database operation and isolation behavior. Record the exact revision, migration versions and check results.

Operator routine actions are Secrets setup and merge-request approval. Digest copy/paste and Run workflow clicks are maintainer interfaces, not the required path. Local Node, Python, Podman, PostgreSQL and Terraform installations are optional maintainer tools, described in [maintainer verification](maintainer-verification.md).

## Current implementation and gaps

| Stage | Current evidence and implementation | Remaining requirement |
| --- | --- | --- |
| Hostname and database intake | Tracked `onboarding.config.json` carries hostname/zone/`auto_apply`; approved database target and public Supabase configuration via Secrets; optional `database-onboarding.config.json` enables read-only catalog preflight ([database onboarding](database-onboarding.md)) | Database connection/migration/RLS orchestration still lacks a live workflow before claiming full onboarding; catalog preflight is not readiness |
| Quality CI | `app-quality.yml` installs Podman on the runner and tests against disposable PostgreSQL; merge onboarding waits for current-head success | These tests remain fixture evidence, separate from the approved Supabase target |
| Shell deployment | `merge-onboarding.yml` deploys under merge policy; `launch-app.yml` remains maintainer dispatch | Shell success is an intermediate result |
| Hostname and DNS | Merge onboarding applies/verifies configured hostname; `custom-domain.yml` remains digest-based maintainer path | Confirm verify evidence per application hostname |
| App-scoped schema migrations | Foundation + delivery libraries (`docs/managed-database-foundation.md`, `docs/managed-database-delivery.md`); merge wires enroll → authorized delivery → deploy when reviewed config enables auto_apply; template `database-delivery.config.json` stays disabled; `.github/workflows/deploy.yml` explicitly disables live migrations | Activate via reviewed delivery config PR; prove deployed HTTP/JWT data-path before claiming full onboarding |
| Live database and isolation verification | Shell health and public configuration checks do not exercise application tables | Add protected Actions checks against the deployed application and approved database using real runtime identities |
| Combined readiness | Merge onboarding reports infrastructure/DNS separately from mandatory DB incompleteness | Do not treat green shell+DNS as full-app-ready |

This document defines the required contract and records the missing implementation. It does not enable the disabled migration workflow or claim the full path already runs.

## Database safety within normal onboarding

Connecting the database and applying app-owned migrations are normal onboarding requirements. Scope them to the approved database and namespace. Use a restricted migration identity, review migration SQL, preserve unrelated applications and shared database lifecycle, and retain migration evidence and an appropriate recovery plan.

Runtime application access obeys RLS. A service-role credential bypasses RLS and is unsuitable for isolation acceptance tests. Privileged database maintenance and Terraform administration retain separate review boundaries.
