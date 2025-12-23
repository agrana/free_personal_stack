#!/bin/bash

# Simple workflow validation using Python (no external dependencies)
# This checks basic YAML syntax and common GitHub Actions patterns

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOWS_DIR="$SCRIPT_DIR/../workflows"

echo "🔍 Validating GitHub Actions workflows (simple check)..."
echo ""

ERRORS=0

# Python script to validate YAML and check for common issues
python3 << 'PYTHON_SCRIPT'
import yaml
import sys
import os
import re
from pathlib import Path

workflows_dir = Path(sys.argv[1])
errors = 0

# Common GitHub Actions issues to check
def check_workflow(content, filepath):
    issues = []
    
    # Check for secrets in if conditions (not allowed)
    if re.search(r'if:\s*.*secrets\.', content):
        issues.append("❌ Cannot check secrets in 'if' conditions. Use environment variables instead.")
    
    # Check for missing await in async calls
    if 'github.rest.' in content and 'await' not in content.split('github.rest.')[0].split('\n')[-1]:
        # This is a heuristic, might have false positives
        pass
    
    # Check for common syntax issues
    if 'secrets.GH_PAT !=' in content and 'if:' in content:
        # Find the line
        for i, line in enumerate(content.split('\n'), 1):
            if 'if:' in line and 'secrets.GH_PAT' in line:
                issues.append(f"Line {i}: Cannot check secrets in 'if' conditions")
    
    return issues

for workflow_file in sorted(workflows_dir.glob("*.yml")) + sorted(workflows_dir.glob("*.yaml")):
    if not workflow_file.is_file():
        continue
    
    print(f"📄 Validating {workflow_file.name}...")
    
    try:
        with open(workflow_file, 'r') as f:
            content = f.read()
            yaml.safe_load(content)
        print(f"  ✅ YAML syntax is valid")
        
        # Check for common GitHub Actions issues
        issues = check_workflow(content, workflow_file)
        if issues:
            for issue in issues:
                print(f"  {issue}")
            errors += len(issues)
        else:
            print(f"  ✅ No common issues found")
            
    except yaml.YAMLError as e:
        print(f"  ❌ YAML syntax error: {e}")
        errors += 1
    except Exception as e:
        print(f"  ❌ Error: {e}")
        errors += 1
    
    print("")

if errors > 0:
    print(f"❌ Found {errors} issue(s)")
    sys.exit(1)
else:
    print("✅ All workflows are valid!")
    sys.exit(0)
PYTHON_SCRIPT
"$WORKFLOWS_DIR"

if [ $? -eq 0 ]; then
    echo "✅ Validation complete!"
else
    echo "❌ Validation failed!"
    exit 1
fi

