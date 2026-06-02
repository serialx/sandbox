# File Commands Reference

## Overview

Comprehensive file operations: read, write, search, replace, find, grep, glob, list, upload, and download.

## Commands

### `aio file read <path>`

Read file content from the sandbox.

| Option | Description |
|--------|-------------|
| `--start <n>` | Start line (0-based) |
| `--end <n>` | End line (not inclusive) |

```bash
aio file read /workspace/main.py
aio file read /workspace/main.py --start 10 --end 30
```

### `aio file write <path>`

Write content to a file.

| Option | Description | Default |
|--------|-------------|---------|
| `--content <text>` | Content to write | - |
| `--stdin` | Read content from stdin | false |
| `--append` | Append instead of overwrite | false |
| `--encoding <enc>` | Encoding: utf-8, base64, raw | utf-8 |

```bash
aio file write /workspace/hello.txt --content "Hello World"
aio file write /workspace/data.csv --stdin < local.csv
aio file write /workspace/log.txt --content "new line" --append
aio file write /workspace/image.png --content "base64data..." --encoding base64
```

### `aio file replace <path>`

Replace text in a file.

| Option | Required | Description |
|--------|----------|-------------|
| `--old <text>` | Yes | Text to find |
| `--new <text>` | Yes | Replacement text |

```bash
aio file replace /workspace/config.py --old "DEBUG=True" --new "DEBUG=False"
aio file replace /workspace/index.html --old "v1.0" --new "v2.0"
```

### `aio file search <path>`

Search within a single file using regex.

| Option | Required | Description |
|--------|----------|-------------|
| `--regex <pattern>` | Yes | Regex pattern to search |

```bash
aio file search /workspace/app.py --regex "def\s+\w+"
aio file search /workspace/config.json --regex '"port":\s*\d+'
```

### `aio file find <path>`

Find files by name using glob pattern (legacy, prefer `glob`).

| Option | Required | Description |
|--------|----------|-------------|
| `--glob <pattern>` | Yes | Glob pattern to match |

```bash
aio file find /workspace --glob "**/*.py"
aio file find /workspace/src --glob "*.test.ts"
```

### `aio file grep <pattern> <path>`

Search file contents across multiple files with regex. Powered by ripgrep when available.

| Option | Description | Default |
|--------|-------------|---------|
| `--include <globs...>` | File patterns to include | all files |
| `--exclude <globs...>` | Patterns to exclude | none |
| `-i, --ignore-case` | Case insensitive | false |
| `-F, --fixed-strings` | Treat pattern as literal string | false |
| `-B <n>` | Lines of context before match (0-20) | 0 |
| `-A <n>` | Lines of context after match (0-20) | 0 |
| `--max-results <n>` | Maximum matches (1-10000) | 500 |
| `--max-file-size <size>` | Skip files larger than this | 1M |
| `--no-recursive` | Do not search recursively | recursive |

```bash
# Basic search
aio file grep "import torch" /workspace --include "*.py"

# Case insensitive with context
aio file grep "todo|fixme" /workspace -i -B 1 -A 3

# Fixed string (no regex)
aio file grep "console.log(" /workspace -F --include "*.js" "*.ts"

# Exclude directories
aio file grep "SELECT.*FROM" /workspace --exclude node_modules .git dist

# Limit results
aio file grep "error" /var/log --max-results 20
```

**Response fields:** `matches[]` (with `file`, `line_number`, `line_content`, `context_before`, `context_after`), `match_count`, `files_searched`, `files_matched`, `truncated`.

### `aio file glob <pattern> <path>`

Find files matching glob patterns with optional metadata.

| Option | Description | Default |
|--------|-------------|---------|
| `--exclude <globs...>` | Patterns to exclude | none |
| `--hidden` | Include hidden files | false |
| `--dirs` | Include directories | false (files only) |
| `--metadata` | Include size and modified time | false |
| `--max-results <n>` | Maximum results (1-50000) | 5000 |
| `--sort <by>` | Sort by: path, name, size, modified | path |
| `--desc` | Sort descending | false |

```bash
# Find all TypeScript files
aio file glob "**/*.ts" /workspace

# With metadata, sorted by modification time
aio file glob "**/*.py" /workspace --metadata --sort modified --desc

# Include hidden files and directories
aio file glob "**/*" /workspace --hidden --dirs --max-results 100

# Exclude patterns
aio file glob "**/*.js" /workspace --exclude node_modules dist .git
```

**Response fields:** `files[]` (with `path`, `name`, `is_directory`, `size?`, `modified_time?`), `total_count`, `truncated`.

### `aio file list <path>`

List directory contents with flexible options.

| Option | Description | Default |
|--------|-------------|---------|
| `--recursive` | List recursively | false |
| `--depth <n>` | Max depth for recursive listing | unlimited |
| `--sort <by>` | Sort by: name, size, modified, type | name |

```bash
aio file list /workspace
aio file list /workspace --recursive --depth 2
aio file list /workspace --sort modified
```

### `aio file upload <local-path>`

Upload a local file to the sandbox.

| Option | Description |
|--------|-------------|
| `--to <remote-path>` | Remote destination path |

```bash
aio file upload ./model.pth --to /workspace/models/model.pth
aio file upload ./data.csv
```

### `aio file download <remote-path>`

Download a file from the sandbox.

| Option | Description |
|--------|-------------|
| `-o, --output <path>` | Local file path to save to |

```bash
aio file download /workspace/result.csv -o ./result.csv
aio file download /workspace/output.png
```

## grep vs search vs find vs glob

| Command | Scope | Purpose |
|---------|-------|---------|
| `grep` | Multi-file | Search file **contents** across directory tree |
| `search` | Single file | Search within one file with regex |
| `find` | Multi-file | Find files by **name** pattern (legacy) |
| `glob` | Multi-file | Find files by **name** pattern (enhanced, with metadata) |
