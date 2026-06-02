# Configuration

API URL resolution, config files, output modes, and environment variables.

**Related**: [SKILL.md](../SKILL.md) for quick start.

## Contents

- [API URL Resolution](#api-url-resolution)
- [Config File](#config-file)
- [Environment Variables](#environment-variables)
- [Output Modes](#output-modes)
- [Verbose Mode](#verbose-mode)
- [Piping and Scripting](#piping-and-scripting)

## API URL Resolution

Priority order (highest to lowest):

1. `--api-url` flag
2. `AIO_BASE_URL` environment variable
3. Config file (`.aiorc`, `aio.config.js`, etc.)
4. Default: `http://localhost:8000`

```bash
# Flag override (highest priority)
aio --api-url http://sandbox:8000 bash "ls"

# Environment variable
export AIO_BASE_URL=http://sandbox:8000
aio bash "ls"

# Use default (http://localhost:8000)
aio bash "ls"
```

## Config File

Config is discovered via [cosmiconfig](https://github.com/cosmiconfig/cosmiconfig). Supported formats:

- `.aiorc` (JSON or YAML)
- `.aiorc.json`
- `.aiorc.yaml`
- `.aiorc.yml`
- `.aiorc.js`
- `.aiorc.cjs`
- `aio.config.js`
- `aio.config.cjs`
- `package.json` (`"aio"` key)

### Example `.aiorc`

```json
{
  "apiUrl": "http://sandbox:8000",
  "output": "text"
}
```

### Example `aio.config.js`

```javascript
export default {
  apiUrl: process.env.SANDBOX_URL || "http://localhost:8000",
  output: "json",
};
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `AIO_BASE_URL` | API base URL | `http://localhost:8000` |

## Output Modes

Three output formats:

| Mode | When | Description |
|------|------|-------------|
| `text` | TTY (terminal) | Human-readable, colored, key-value tables |
| `json` | Piped/redirected | Full JSON response for scripting |
| `table` | Explicit only | Tabular format for arrays |

### Auto-Detection

```bash
# Terminal → text mode (colored, formatted)
aio bash "ls -la"

# Piped → JSON mode (machine-readable)
aio bash "ls -la" | jq '.output'

# Redirected → JSON mode
aio sandbox info > info.json
```

### Explicit Override

```bash
aio bash "ls" --output json      # Force JSON
aio bash "ls" --output text      # Force text
aio file list /workspace --output table  # Force table
```

## Verbose Mode

Show HTTP request/response details (written to stderr):

```bash
aio -v bash "echo hello"
# [verbose] POST http://localhost:8000/v1/shell/exec
# [verbose] Body: {"command":"echo hello"}
# [verbose] Response: 200 OK
# [verbose] Data: {"id":"abc","output":"hello\n","exit_code":0}
# hello
```

## Piping and Scripting

### JSON Processing with jq

```bash
# Get specific field
aio sandbox info --output json | jq -r '.version'

# Extract file list
aio file list /workspace --output json | jq -r '.[] | .name'

# Count grep matches
aio file grep "TODO" /workspace --include "*.py" --output json | jq '.match_count'
```

### Chaining Commands

```bash
# Find files, then read each
for f in $(aio file glob "**/*.py" /workspace --output json | jq -r '.files[].path'); do
  echo "=== $f ==="
  aio file read "$f"
done
```

### Exit Codes

- `0` — Success
- `1` — API error, connection failure, or command error
- Shell commands forward the remote exit code in text mode

```bash
aio bash "python -c 'exit(42)'"
echo $?  # 42
```

### Conditional Execution

```bash
# Only proceed if grep finds matches
if aio file grep "class MyModel" /workspace --include "*.py" --output json | jq -e '.match_count > 0' > /dev/null; then
  echo "Found MyModel"
else
  echo "Not found"
fi
```
