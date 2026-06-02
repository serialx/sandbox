# Sandbox Reference

## Container Management (outside sandbox only)

### create

```bash
aio sandbox create dev                                    # Default port 8080
aio sandbox create dev --port 18080                       # Custom port
aio sandbox create dev --image sandbox:latest             # Image
aio sandbox create dev --cpus 4 --memory 8g --shm-size 8g
aio sandbox create dev -e TZ=Asia/Shanghai -e TOKEN=xxx   # Env vars
aio sandbox create dev -v ~/proj:/home/gem/proj           # Volumes
```

### list / status / logs

```bash
aio sandbox list          # Table: NAME / STATUS / PORT / IMAGE
aio sandbox status dev    # Detailed info + HTTP health check
aio sandbox logs dev --tail 50
aio sandbox logs dev --follow
```

### stop / rm

```bash
aio sandbox stop dev
aio sandbox rm dev
aio sandbox rm dev --force    # Remove even if running
```

## Environment Info (always available)

```bash
aio sandbox info                  # Sandbox metadata
aio sandbox packages --python     # Python packages
aio sandbox packages --nodejs     # Node.js packages
```

## Common Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TZ` | `Asia/Singapore` | Timezone |
| `WORKSPACE` | `/home/gem` | Working directory |
| `GITHUB_TOKEN` | | GitHub access token |
| `HOMEPAGE` | | Browser start page |
| `RUN_HOOK_POST_READY` | | Post-ready hook script |
| `MAX_SHELL_SESSIONS` | `10` | Max shell sessions |
| `AIO_SKILLS_PATH` | | Auto-register skills path |

## Web UI

```bash
aio web                   # Open http://localhost:8080
aio web --port 18080      # Custom port
```
