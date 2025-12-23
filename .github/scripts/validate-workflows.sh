#!/bin/bash

# Validate GitHub Actions workflow files
# Checks for common YAML and GitHub Actions syntax issues

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOWS_DIR="$SCRIPT_DIR/../workflows"

echo "🔍 Validating GitHub Actions workflows..."
echo ""

# Check if actionlint is available
if command -v actionlint &> /dev/null; then
    echo "✅ Using actionlint for validation..."
    echo ""
    
    # Run actionlint and capture output
    OUTPUT=$(actionlint "$WORKFLOWS_DIR"/*.yml 2>&1 || true)
    
    # Separate errors from warnings
    ERRORS=$(echo "$OUTPUT" | grep -E "error|syntax-check" || true)
    WARNINGS=$(echo "$OUTPUT" | grep "shellcheck reported issue" || true)
    
    if [ -n "$ERRORS" ]; then
        echo "$ERRORS"
        echo ""
        echo "❌ Found workflow errors!"
        exit 1
    fi
    
    if [ -n "$WARNINGS" ]; then
        WARNING_COUNT=$(echo "$WARNINGS" | wc -l | tr -d ' ')
        echo "⚠️  Found $WARNING_COUNT shellcheck style warning(s) (not blocking)"
        echo "   These are code style suggestions, not errors"
        echo "   Run 'actionlint .github/workflows/*.yml' to see all warnings"
        echo ""
    fi
    
    echo "✅ All workflows passed actionlint validation!"
    exit 0
fi

# Fallback to basic validation if actionlint is not available
echo "⚠️  actionlint not found. Using basic validation..."
echo "   Install with: brew install actionlint"
echo ""

ERRORS=0

# Function to check for common issues
check_workflow() {
    local file="$1"
    local filename=$(basename "$file")
    local issues=0
    
    echo "📄 Checking $filename..."
    
    # Check 1: Basic YAML structure (check for balanced brackets/braces)
    if ! grep -q "^name:" "$file"; then
        echo "  ⚠️  Warning: Missing 'name' field"
    fi
    
    # Check 2: Cannot use secrets in if conditions
    if grep -E "^\s+if:\s*.*secrets\." "$file" > /dev/null; then
        echo "  ❌ ERROR: Cannot check secrets in 'if' conditions"
        echo "     Found on line(s):"
        grep -n -E "^\s+if:\s*.*secrets\." "$file" | sed 's/^/       /'
        issues=$((issues + 1))
    fi
    
    # Check 3: Check for proper await usage (heuristic)
    if grep -q "github\.rest\." "$file" && ! grep -q "await github\.rest\." "$file"; then
        echo "  ⚠️  Warning: github.rest calls should use 'await'"
    fi
    
    # Check 4: Check for ${{ }} syntax issues
    if grep -E '\$\{\{\s*secrets\.[^}]+\s*!=\s*['\''"]\s*['\''"]\s*\}\}' "$file" > /dev/null; then
        echo "  ❌ ERROR: Cannot check secrets in expressions like this"
        echo "     Use environment variables in shell scripts instead"
        grep -n -E '\$\{\{\s*secrets\.[^}]+\s*!=\s*['\''"]\s*['\''"]\s*\}\}' "$file" | sed 's/^/       /'
        issues=$((issues + 1))
    fi
    
    # Check 5: Validate basic YAML structure (check for common syntax errors)
    if command -v python3 &> /dev/null; then
        if python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>&1 | grep -q "error\|Error"; then
            echo "  ❌ YAML syntax error:"
            python3 -c "import yaml; yaml.safe_load(open('$file'))" 2>&1 | sed 's/^/       /'
            issues=$((issues + 1))
        fi
    fi
    
    if [ $issues -eq 0 ]; then
        echo "  ✅ No issues found"
    fi
    
    return $issues
}

# Validate each workflow file
for workflow in "$WORKFLOWS_DIR"/*.yml "$WORKFLOWS_DIR"/*.yaml; do
    if [ -f "$workflow" ]; then
        check_workflow "$workflow"
        ERRORS=$((ERRORS + $?))
        echo ""
    fi
done

if [ $ERRORS -eq 0 ]; then
    echo "✅ All workflows passed validation!"
    exit 0
else
    echo "❌ Found issues in $ERRORS workflow(s)"
    echo ""
    echo "💡 Tips:"
    echo "   - Cannot check secrets in 'if' conditions - use env vars in shell scripts instead"
    echo "   - Use 'await' for async GitHub API calls"
    echo "   - Install 'actionlint' for more comprehensive validation: brew install actionlint"
    exit 1
fi
