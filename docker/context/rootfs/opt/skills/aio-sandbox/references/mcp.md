# MCP Commands

Manage MCP (Model Context Protocol) servers and execute tools.

**Related**: [SKILL.md](../SKILL.md) for quick start.

## Commands

### `aio mcp list`

List all configured MCP servers.

```bash
aio mcp list
aio mcp list --output json
```

### `aio mcp tools <server>`

List available tools from a specific MCP server.

```bash
aio mcp tools my-server
aio mcp tools my-server --output json
```

**Output includes**: tool name, description, and input schema for each tool.

### `aio mcp call <server> <tool>`

Execute a tool on an MCP server.

```bash
# Simple call (no arguments)
aio mcp call my-server get_info

# With arguments
aio mcp call my-server search --args '{"query": "hello", "limit": 10}'

# Complex arguments
aio mcp call code-server execute --args '{"code": "print(1+1)", "language": "python"}'
```

| Option | Description |
|--------|-------------|
| `--args <json>` | Tool arguments as JSON string (default: `"{}"`) |

### `aio mcp add <name>`

Add an MCP server to the configuration file (`mcp-servers.json`).

```bash
# Add an HTTP server
aio mcp add my-api --url https://mcp.example.com

# Add an SSE server
aio mcp add events --url https://mcp.example.com/sse --type sse

# Add a stdio server
aio mcp add local-tool --command python --args mcp_server.py

# With environment variables
aio mcp add my-server --url https://api.example.com --env API_KEY=sk-xxx TOKEN=abc

# Custom config path
aio mcp add my-server --url https://example.com --config /workspace/mcp-servers.json
```

| Option | Description |
|--------|-------------|
| `--url <url>` | Server URL (for HTTP/SSE servers) |
| `--command <cmd>` | Command to run (for stdio servers, auto-sets type to `stdio`) |
| `--args <args...>` | Command arguments (for stdio servers) |
| `--type <type>` | Server type: `streamable-http`, `sse`, `stdio` (default: `streamable-http`) |
| `--env <pairs...>` | Environment variables as `KEY=VALUE` pairs |
| `--config <path>` | Config file path in sandbox (default: `mcp-servers.json`) |

## Configuration File Format

The `mcp-servers.json` file structure:

```json
{
  "mcpServers": {
    "server-name": {
      "url": "https://mcp.example.com",
      "type": "streamable-http"
    },
    "local-tool": {
      "command": "python",
      "args": ["mcp_server.py"],
      "type": "stdio",
      "env": {
        "API_KEY": "sk-xxx"
      }
    }
  }
}
```

### Server Types

| Type | Description | Required Fields |
|------|-------------|-----------------|
| `streamable-http` | HTTP-based MCP server (default) | `url` |
| `sse` | Server-Sent Events transport | `url` |
| `stdio` | Local process via stdin/stdout | `command` |

## Common Patterns

### Discover and use tools

```bash
# 1. List available servers
aio mcp list

# 2. See what tools a server offers
aio mcp tools my-server

# 3. Call a specific tool
aio mcp call my-server tool_name --args '{"key": "value"}'
```

### Add a server and verify

```bash
# Add the server
aio mcp add weather --url https://weather-mcp.example.com --type sse

# Verify it's listed
aio mcp list

# Check available tools
aio mcp tools weather

# Use a tool
aio mcp call weather get_forecast --args '{"city": "Tokyo"}'
```
