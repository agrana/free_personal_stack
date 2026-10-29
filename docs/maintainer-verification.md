# Optional maintainer verification

## GitHub Actions is the default

Operators use GitHub settings and Actions for application onboarding. **App Quality (APP-006)** installs its own Node, Python and Podman dependencies and creates disposable PostgreSQL on the runner. Its `APP003_PG_TESTS=1` setting makes missing or skipped database tests fail the workflow.

Podman runs the isolated maintainer test database. The approved Supabase target is verified separately during required live onboarding. See [the end-to-end contract](end-to-end-onboarding.md).

## Reproduce quality checks locally

These commands are for maintainers who choose local reproduction. Node.js 24.x is declared in `package.json`; inspect dependency scripts before installation.

```bash
npm ci
python3 -m venv .venv
.venv/bin/pip install -r requirements-bootstrap.txt
npm run lint
npm run type-check
APP003_PG_TESTS=1 .venv/bin/python scripts/run-all-tests.py
npm run build
```

The runner includes hyphenated and underscored test filenames. A focused check is:

```bash
.venv/bin/python -m unittest tests.template_shell_test tests.workflow_safety_test -v
```

Local disposable database tests use Podman and PostgreSQL; inspect `tests/shared_db_isolation_test.py`. `APP003_PG_TESTS=1` requires the integration tests instead of allowing a skip. Report missing prerequisites and skipped tests separately.

Retained auth routes require public Supabase build configuration. Use explicitly labelled synthetic local test values and keep production credentials out of maintainer tests. The database-free shell does not query application tables. Health tests use the server-only `APP006_LOCAL_TEST_REVISION` fixture; live verification must use the actual production revision.

## Advanced infrastructure checks

```bash
./scripts/validate-app-config.sh --config examples/app.config.example.json --mode draft
terraform -chdir=terraform fmt -check -recursive
terraform -chdir=terraform init -backend=false
terraform -chdir=terraform validate
```

Provider initialization downloads dependencies. These checks establish local configuration validity; production permissions and deployments require their own verified Actions evidence.
