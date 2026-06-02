# Shell Sessions

Session lifecycle, async execution, long-running tasks, and process management.

**Related**: [bash.md](bash.md) for full command reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Session Model](#session-model)
- [Async Execution](#async-execution)
- [Long-Running Tasks](#long-running-tasks)
- [Session Reuse](#session-reuse)
- [Process Management](#process-management)
- [Common Patterns](#common-patterns)
- [Best Practices](#best-practices)

## Session Model

Each shell command runs inside a tmux session. Sessions can be:

- **Auto-created**: `aio bash "ls"` creates a temporary session
- **Named/persistent**: `aio bash session-create --id dev` creates a reusable session
- **Reused**: `aio bash exec "make build" --id dev` runs in an existing session

## Async Execution

By default, commands block until completion. Use `--async` for non-blocking execution:

```bash
# Synchronous (blocks until done)
aio bash "python train.py"          # Waits for completion

# Asynchronous (returns immediately)
aio bash exec "python train.py" --async --id training
# Returns: {"id": "training", "status": "running"}

# Check output later
aio bash view training
```

## Long-Running Tasks

### Start and Monitor

```bash
# Start a long task
aio bash exec "python train.py --epochs 100" --async --id training --timeout 3600

# Check progress periodically
aio bash view training

# Kill if needed
aio bash kill training
```

### With Timeout

```bash
# 5-minute timeout
aio bash exec "pytest tests/" --timeout 300

# 1-hour timeout for training
aio bash exec "python train.py" --timeout 3600 --async --id train
```

## Session Reuse

### Dedicated Work Sessions

```bash
# Create a session with a working directory
aio bash session-create --id project --dir /workspace/myproject

# Run multiple commands in the same session
aio bash exec "pip install -r requirements.txt" --id project
aio bash exec "python -m pytest" --id project
aio bash exec "python main.py" --id project

# Clean up when done
aio bash session-delete project
```

### Session List and Cleanup

```bash
# See all active sessions
aio bash sessions

# Delete specific session
aio bash session-delete my-session

# View output from any session
aio bash view my-session
```

## Process Management

### Kill Running Process

```bash
# Start a long-running process
aio bash exec "python server.py" --async --id server

# Kill it
aio bash kill server
```

### Check Session Output

```bash
# View full output
aio bash view my-session

# View in JSON format for parsing
aio bash view my-session --output json
```

## Common Patterns

### Build and Test Pipeline

```bash
#!/bin/bash
SESSION="ci"
aio bash session-create --id $SESSION --dir /workspace

# Install dependencies
aio bash exec "pip install -r requirements.txt" --id $SESSION --timeout 120
if [ $? -ne 0 ]; then
  echo "Install failed"
  aio bash session-delete $SESSION
  exit 1
fi

# Run tests
aio bash exec "python -m pytest tests/ -v" --id $SESSION --timeout 300

# Cleanup
aio bash session-delete $SESSION
```

### Background Service

```bash
# Start a background service
aio bash exec "python -m http.server 9000" --async --id http-server

# Do work that depends on the service
aio bash "curl -s http://localhost:9000/status"

# Clean up
aio bash kill http-server
aio bash session-delete http-server
```

### Multi-Step Data Processing

```bash
aio bash session-create --id etl --dir /workspace/data

aio bash exec "python extract.py" --id etl --timeout 300
aio bash exec "python transform.py" --id etl --timeout 300
aio bash exec "python load.py" --id etl --timeout 300

aio bash session-delete etl
```

## Best Practices

1. **Use named sessions for related commands**: Group related work in a single session
2. **Set appropriate timeouts**: Don't let commands hang forever — use `--timeout`
3. **Use `--async` for long tasks**: Don't block on training, builds, or servers
4. **Clean up sessions**: Delete sessions when done to free resources
5. **Check exit codes**: Use `$?` to verify command success in scripts
6. **Use `--dir` for context**: Set the working directory to avoid `cd` chains
