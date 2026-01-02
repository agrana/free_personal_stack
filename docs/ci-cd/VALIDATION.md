# Workflow Validation

This directory contains scripts to validate GitHub Actions workflow files locally before pushing.

## Quick Validation

Run the validation script:

```bash
.github/scripts/validate-workflows.sh
```

## What It Checks

1. **YAML Syntax** - Basic YAML structure validation
2. **Secrets in Conditions** - Detects invalid `if: secrets.XXX` patterns
3. **Async/Await** - Warns about missing `await` in GitHub API calls
4. **Expression Syntax** - Checks for invalid `${{ }}` expressions

## Common Issues Fixed

### ❌ Cannot Check Secrets in `if` Conditions

**Wrong:**
```yaml
- name: Step
  if: secrets.GH_PAT != ''
```

**Correct:**
```yaml
- name: Step
  env:
    GH_PAT: ${{ secrets.GH_PAT }}
  run: |
    if [ -z "$GH_PAT" ]; then
      exit 0
    fi
```

### ❌ Secrets in Expressions

**Wrong:**
```yaml
GH_PAT_PROVIDED="${{ secrets.GH_PAT != '' }}"
```

**Correct:**
```yaml
GH_PAT_PROVIDED="false"
if [ -n "$GH_PAT" ]; then
  GH_PAT_PROVIDED="true"
fi
```

## Advanced Validation with actionlint

The validation script automatically uses `actionlint` if installed:

```bash
# macOS
brew install actionlint

# Or via Go
go install github.com/rhymond/actionlint/cmd/actionlint@latest
```

The script will:
- ✅ Check for YAML syntax errors
- ✅ Check for GitHub Actions syntax errors
- ✅ Check for invalid expressions
- ⚠️  Report shellcheck style warnings (not blocking)

You can also run actionlint directly:

```bash
# Validate all workflows
actionlint .github/workflows/*.yml

# Validate a specific file
actionlint .github/workflows/setup.yml
```

## GitHub's Built-in Validation

GitHub automatically validates workflows when you:
- Push to a branch
- Open a pull request
- View the Actions tab

However, local validation saves time by catching errors before pushing.

