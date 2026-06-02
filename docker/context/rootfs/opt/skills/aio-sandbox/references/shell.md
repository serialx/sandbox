# Shell & TTY Reference

## aio shell — Pipe-based execution

Calls `/v1/bash/*` API. Separate stdout/stderr, stdin write support, signal control.

### exec

```bash
aio shell "ls -la"                              # Shorthand
aio shell exec "command"                        # Full form
aio shell exec "cmd" --timeout 120              # HTTP timeout
aio shell exec "cmd" --hard-timeout 60          # Kill process after 60s
aio shell exec "cmd" --async                    # Return immediately
aio shell exec "cmd" --dir /workspace           # Working directory
aio shell exec "cmd" --id my-session            # Use specific session
```

### Async workflow

```bash
aio shell exec "python train.py" --async        # Returns session_id
aio shell output <sid> --wait                    # Long-poll for output
aio shell output <sid> --offset 1024             # Read from byte offset
aio shell output <sid> --stderr-offset 0         # Read stderr
aio shell write <sid> "yes\n"                    # Write to stdin
aio shell kill <sid>                             # Send SIGTERM
aio shell kill <sid> --signal SIGKILL            # Force kill
aio shell kill <sid> --signal SIGINT             # Ctrl-C
```

### Sessions

```bash
aio shell sessions                               # List all
aio shell session-create                         # Auto-generated ID
aio shell session-create --id dev --dir /src     # Custom ID + dir
aio shell session-close <id>                     # Close session
```

## aio tty — Tmux-based terminal

Calls `/v1/shell/*` API. Merged stdout/stderr, persistent tmux sessions.

### exec

```bash
aio tty "npm test"                               # Shorthand
aio tty exec "command"                           # Full form
aio tty exec "cmd" --timeout 120                 # Timeout
aio tty exec "cmd" --async                       # Async
aio tty exec "cmd" --id my-session               # Session
aio tty exec "cmd" --dir /workspace              # Working dir
```

### Sessions

```bash
aio tty sessions                                  # List sessions
aio tty session-create --id dev --dir /src        # Create session
aio tty view <session-id>                         # View output
aio tty kill <session-id>                         # Kill process
aio tty session-delete <session-id>               # Delete session
```

## When to use which

| Scenario | Use |
|----------|-----|
| Single command, need exit code | `aio shell` |
| Need separate stdout/stderr | `aio shell` |
| Need stdin write | `aio shell` |
| Need signal control (SIGINT/SIGKILL) | `aio shell` |
| Interactive session | `aio tty` |
| Long-running process | `aio tty` |
| Backward compat with `aio bash` | `aio tty` (alias) |
