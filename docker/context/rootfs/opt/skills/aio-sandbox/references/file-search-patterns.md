# File Search Patterns

Strategies for searching, finding, and navigating codebases using grep, glob, find, and search.

**Related**: [file.md](file.md) for full command reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Choosing the Right Tool](#choosing-the-right-tool)
- [grep Patterns](#grep-patterns)
- [glob Patterns](#glob-patterns)
- [search Patterns](#search-patterns)
- [Codebase Exploration](#codebase-exploration)
- [Refactoring Workflows](#refactoring-workflows)
- [Best Practices](#best-practices)

## Choosing the Right Tool

| Need | Command | Example |
|------|---------|---------|
| Search file **contents** across many files | `grep` | Find all `import torch` |
| Find files by **name/path** pattern | `glob` | Find all `*.test.ts` files |
| Search within a **single file** | `search` | Find functions in `app.py` |
| Simple file name search (legacy) | `find` | Find `*.py` files |
| Browse **directory** structure | `list` | See what's in `/workspace` |

## grep Patterns

### Basic Searches

```bash
# Find all imports of a module
aio file grep "import requests" /workspace --include "*.py"

# Find function definitions
aio file grep "def\s+\w+" /workspace --include "*.py"

# Find class definitions
aio file grep "class\s+\w+.*:" /workspace --include "*.py"

# Find TODO comments across all code
aio file grep "TODO|FIXME|HACK|XXX" /workspace -i
```

### With Context Lines

```bash
# See function signatures with 3 lines of body
aio file grep "def\s+process" /workspace --include "*.py" -A 3

# See imports and the code that uses them
aio file grep "from\s+config\s+import" /workspace --include "*.py" -A 2

# See error handling patterns
aio file grep "except\s+\w+Error" /workspace --include "*.py" -B 2 -A 5
```

### Filtering Files

```bash
# Search only Python files
aio file grep "logging" /workspace --include "*.py"

# Search multiple file types
aio file grep "fetchData" /workspace --include "*.ts" "*.tsx" "*.js"

# Exclude directories
aio file grep "console.log" /workspace --exclude node_modules dist .git __pycache__

# Combine include and exclude
aio file grep "import" /workspace --include "*.py" --exclude tests venv
```

### Literal vs Regex

```bash
# Regex (default): special chars have meaning
aio file grep "price\s*=\s*\d+" /workspace --include "*.py"

# Fixed string (-F): search for exact text
aio file grep "console.log(" /workspace -F --include "*.js"

# Fixed string is safer for special characters
aio file grep "arr[0]" /workspace -F --include "*.py"
```

### Controlling Results

```bash
# Limit results
aio file grep "error" /var/log --max-results 20

# Skip large files
aio file grep "pattern" /workspace --max-file-size 500K

# Non-recursive (current directory only)
aio file grep "main" /workspace --no-recursive
```

## glob Patterns

### Basic Patterns

```bash
# All Python files recursively
aio file glob "**/*.py" /workspace

# All files in src/ (one level)
aio file glob "src/*" /workspace

# All test files
aio file glob "**/*test*" /workspace
aio file glob "**/test_*.py" /workspace
aio file glob "**/*.test.ts" /workspace

# All config files
aio file glob "**/*.{json,yaml,yml,toml}" /workspace
```

### With Metadata

```bash
# See file sizes
aio file glob "**/*.py" /workspace --metadata

# Find recently modified files
aio file glob "**/*" /workspace --metadata --sort modified --desc --max-results 20

# Find largest files
aio file glob "**/*" /workspace --metadata --sort size --desc --max-results 10
```

### Including Directories

```bash
# Show directory structure
aio file glob "**/*" /workspace --dirs --max-results 100

# Find all directories named "tests"
aio file glob "**/tests" /workspace --dirs
```

### Excluding Patterns

```bash
# Exclude common unwanted directories
aio file glob "**/*.js" /workspace --exclude node_modules dist .git

# Include hidden files
aio file glob "**/*" /workspace --hidden
```

## search Patterns

Single-file regex search:

```bash
# Find all function definitions in one file
aio file search /workspace/main.py --regex "def\s+\w+"

# Find all class methods
aio file search /workspace/models.py --regex "def\s+(get|set|update|delete)\w+"

# Find configuration values
aio file search /workspace/config.py --regex "\w+\s*=\s*['\"].*['\"]"
```

## Codebase Exploration

### Understand Project Structure

```bash
# Top-level overview
aio file list /workspace

# Directory tree (2 levels deep)
aio file list /workspace --recursive --depth 2

# Find all source file types
aio file glob "**/*.py" /workspace --metadata | head -5
aio file glob "**/*.ts" /workspace --metadata | head -5
aio file glob "**/*.go" /workspace --metadata | head -5
```

### Find Entry Points

```bash
# Python entry points
aio file grep "if __name__.*__main__" /workspace --include "*.py"
aio file grep "def main" /workspace --include "*.py"

# JavaScript/TypeScript entry points
aio file grep "export default" /workspace --include "*.ts" "*.tsx"

# Configuration files
aio file glob "**/package.json" /workspace
aio file glob "**/pyproject.toml" /workspace
aio file glob "**/Dockerfile*" /workspace
```

### Trace Dependencies

```bash
# Find all files importing a specific module
aio file grep "from myapp.models import" /workspace --include "*.py"

# Find all usages of a function
aio file grep "process_data\(" /workspace --include "*.py" -A 2

# Find API endpoint definitions
aio file grep "@app\.(get|post|put|delete)" /workspace --include "*.py" -A 1
```

## Refactoring Workflows

### Search Then Replace

```bash
# Step 1: Find all occurrences
aio file grep "old_function_name" /workspace --include "*.py"

# Step 2: Review context
aio file grep "old_function_name" /workspace --include "*.py" -B 1 -A 3

# Step 3: Replace in each file
aio file replace /workspace/src/utils.py --old "old_function_name" --new "new_function_name"
aio file replace /workspace/src/main.py --old "old_function_name" --new "new_function_name"
```

### Find Dead Code

```bash
# Find function definitions
aio file grep "def\s+(\w+)" /workspace/src/utils.py --include "*.py"

# Check each for usage elsewhere
aio file grep "helper_function" /workspace --include "*.py"
# If only found at definition → potentially dead code
```

## Best Practices

1. **Start broad, then narrow**: Begin with a simple grep, then add `--include` and `--exclude` to refine
2. **Use `-F` for literal searches**: Avoid regex escaping headaches
3. **Use context lines**: `-B` and `-A` help understand matches without reading the full file
4. **Use glob for file discovery, grep for content**: Don't grep for filenames, and don't glob for content
5. **Limit results for large codebases**: Use `--max-results` to avoid overwhelming output
6. **Pipe to jq for scripting**: Use `--output json` when processing results programmatically
