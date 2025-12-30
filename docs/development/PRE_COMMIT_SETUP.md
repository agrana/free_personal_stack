# Pre-commit Hooks Setup

This document explains the comprehensive pre-commit hooks setup for both code and infrastructure.

## 🎯 What's Included

### Code Quality Hooks

- **ESLint** - JavaScript/TypeScript linting and fixing
- **Prettier** - Code formatting
- **TypeScript** - Type checking
- **Markdown Lint** - Documentation formatting
- **YAML Lint** - Configuration file validation

### Infrastructure Hooks

- **Terraform Format** - Terraform code formatting
- **Terraform Validate** - Syntax and configuration validation
- **TFLint** - Terraform-specific linting rules
- **TFSec** - Security scanning for Terraform
- **Checkov** - Infrastructure security and compliance
- **Terraform Docs** - Automatic documentation generation

### Security Hooks

- **Detect Secrets** - Prevents committing secrets
- **Security Audit** - NPM security vulnerability scanning
- **Commit Message Linting** - Enforces conventional commits

## 🚀 Quick Setup

### 1. Install Dependencies

```bash
npm install
```

### 2. Install Pre-commit (Optional)

For the full pre-commit experience with all hooks:

```bash
# Install pre-commit
pip install pre-commit

# Install the git hook scripts
pre-commit install

# Install commit-msg hook
pre-commit install --hook-type commit-msg

# Install pre-push hook
pre-commit install --hook-type pre-push
```

### 3. Run Hooks Manually

```bash
# Run all pre-commit hooks
pre-commit run --all-files

# Run specific hooks
pre-commit run terraform_fmt
pre-commit run eslint
pre-commit run prettier
```

## 📋 Available Scripts

### Code Quality

```bash
npm run lint          # Run ESLint
npm run lint:fix      # Fix ESLint issues
npm run type-check    # TypeScript type checking
npm run format        # Format code with Prettier
npm run format:check  # Check formatting
```

### Terraform

```bash
npm run terraform:fmt        # Format Terraform files
npm run terraform:fmt-check  # Check Terraform formatting
npm run terraform:validate   # Validate Terraform syntax
npm run terraform:security   # Run security checks
npm run terraform:docs       # Generate documentation
```

### Pre-commit

```bash
npm run pre-commit        # Run lint-staged (fast)
npm run pre-commit-full   # Run comprehensive checks
```

## 🔧 Configuration Files

### Core Configuration

- `.pre-commit-config.yaml` - Main pre-commit configuration
- `.husky/` - Git hooks directory
- `package.json` - NPM scripts and lint-staged config

### Code Quality

- `.eslintrc.json` - ESLint configuration
- `.prettierrc` - Prettier configuration
- `.prettierignore` - Prettier ignore patterns

### Terraform

- `.tflint.hcl` - TFLint configuration
- `.tfsec.yml` - TFSec security scanning config

### Documentation

- `.markdownlint.json` - Markdown linting rules
- `.yamllint.yml` - YAML linting rules
- `.commitlintrc.js` - Commit message linting

### Security

- `.secrets.baseline` - Secrets detection baseline

## 🎛️ Hook Behavior

### Pre-commit Hook

- Runs on `git commit`
- Executes `lint-staged` for staged files only
- Fast execution for quick commits

### Pre-push Hook

- Runs on `git push`
- Executes comprehensive checks
- Includes security scanning and tests

### Commit Message Hook

- Validates commit message format
- Enforces conventional commit standards
- Provides helpful error messages

## 🚨 Troubleshooting

### Common Issues

1. **Pre-commit not found**

   ```bash
   pip install pre-commit
   pre-commit install
   ```

2. **Terraform tools missing**

   ```bash
   # Install TFLint
   brew install tflint

   # Install TFSec
   brew install tfsec

   # Install Checkov
   pip install checkov
   ```

3. **Hooks too slow**
   - Use `npm run pre-commit` for fast checks
   - Skip comprehensive checks with `SKIP_AUDIT=true`

4. **False positive secrets**
   - Update `.secrets.baseline` with known false positives
   - Use `detect-secrets scan --update .secrets.baseline`

### Bypassing Hooks (Emergency)

```bash
# Skip pre-commit hooks
git commit --no-verify -m "Emergency fix"

# Skip specific checks
SKIP_AUDIT=true git commit -m "Skip security audit"
```

## 📚 Customization

### Adding New Hooks

1. Edit `.pre-commit-config.yaml`
2. Add your hook configuration
3. Run `pre-commit install` to update

### Modifying Rules

- **ESLint**: Edit `.eslintrc.json`
- **Prettier**: Edit `.prettierrc`
- **TFLint**: Edit `.tflint.hcl`
- **TFSec**: Edit `.tfsec.yml`

### Custom Scripts

Add new scripts to `package.json`:

```json
{
  "scripts": {
    "custom-check": "your-custom-command"
  }
}
```

## 🔍 Monitoring

### Check Hook Status

```bash
# List installed hooks
pre-commit install --install-hooks

# Test all hooks
pre-commit run --all-files

# Check specific hook
pre-commit run hook-name
```

### Performance Monitoring

```bash
# Time hook execution
time pre-commit run --all-files

# Profile slow hooks
pre-commit run --verbose hook-name
```

## 🎉 Benefits

- **Consistent Code Quality** - Automated formatting and linting
- **Security** - Prevents secrets and vulnerabilities
- **Documentation** - Auto-generated Terraform docs
- **Compliance** - Infrastructure security scanning
- **Team Standards** - Enforced commit message format
- **Fast Feedback** - Catch issues before they reach CI/CD

## 📖 Resources

- [Pre-commit Documentation](https://pre-commit.com/)
- [Husky Documentation](https://typicode.github.io/husky/)
- [TFLint Documentation](https://github.com/terraform-linters/tflint)
- [TFSec Documentation](https://aquasecurity.github.io/tfsec/)
- [Conventional Commits](https://www.conventionalcommits.org/)

---

**Happy coding with confidence! 🚀**
