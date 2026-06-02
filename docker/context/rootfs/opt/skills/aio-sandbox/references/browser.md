# Browser Commands Reference

## Overview

Playwright SDK browser automation: navigation, element interaction, content extraction, tab management, and wait conditions.

All commands target the sandbox's built-in Chromium browser via the Playwright SDK backend.

## Commands

### Navigation

#### `aio browser navigate <url>`

| Option | Description | Default |
|--------|-------------|---------|
| `--wait-until <event>` | load, domcontentloaded, networkidle, commit | load |
| `--timeout <seconds>` | Navigation timeout | 30 |

```bash
aio browser navigate https://example.com
aio browser navigate https://app.com --wait-until networkidle --timeout 60
```

#### `aio browser restart`

Restart the browser session.

| Option | Description | Default |
|--------|-------------|---------|
| `--mode <mode>` | soft or hard | hard |

```bash
aio browser restart
aio browser restart --mode soft
```

### Screenshots

#### `aio browser screenshot`

Capture a page screenshot (Playwright-level, not desktop).

| Option | Description | Default |
|--------|-------------|---------|
| `--full` | Full page screenshot | false |
| `-o, --output <file>` | Output file path | page-screenshot.png |
| `--format <fmt>` | png or jpeg | png |

```bash
aio browser screenshot
aio browser screenshot --full -o fullpage.png
aio browser screenshot --format jpeg -o page.jpg
```

### Element Interaction

#### `aio browser click <selector>`

| Option | Description | Default |
|--------|-------------|---------|
| `--index <n>` | Element index if multiple matches | - |
| `--button <btn>` | left, right, middle | left |

```bash
aio browser click "button.submit"
aio browser click "li.item" --index 2
aio browser click "#menu" --button right
```

#### `aio browser fill <text>`

Fill an input field (clears existing content first).

| Option | Description |
|--------|-------------|
| `-s, --selector <sel>` | CSS selector for the input |
| `--index <n>` | Element index |

```bash
aio browser fill "user@example.com" -s "#email"
aio browser fill "password123" -s "input[type=password]"
```

#### `aio browser type <text>`

Type text character by character (without clearing).

| Option | Description | Default |
|--------|-------------|---------|
| `--delay <ms>` | Delay between keystrokes | 0 |

```bash
aio browser type "search query" --delay 50
```

#### `aio browser press <key>`

Press a single key.

```bash
aio browser press Enter
aio browser press Tab
aio browser press Escape
```

#### `aio browser hotkey <keys...>`

Press a key combination.

```bash
aio browser hotkey Control a
aio browser hotkey Control Shift i
aio browser hotkey Meta c
```

#### `aio browser scroll`

| Option | Description | Default |
|--------|-------------|---------|
| `--dir <direction>` | up or down | down |
| `--amt <pixels>` | Scroll amount | 300 |

```bash
aio browser scroll
aio browser scroll --dir up --amt 500
```

### Content Extraction

#### `aio browser html`

| Option | Description | Default |
|--------|-------------|---------|
| `--outer` | Get outer HTML | false |

```bash
aio browser html
aio browser html --outer
```

#### `aio browser text`

Get visible text content of the page.

```bash
aio browser text
```

#### `aio browser markdown`

Get page content converted to markdown.

```bash
aio browser markdown
```

#### `aio browser evaluate <js>`

Execute JavaScript in the page context.

```bash
aio browser evaluate "document.title"
aio browser evaluate "document.querySelectorAll('a').length"
aio browser evaluate "JSON.stringify(performance.timing)"
```

#### `aio browser console`

Get browser console logs.

| Option | Description | Default |
|--------|-------------|---------|
| `--clear` | Clear console after reading | false |

```bash
aio browser console
aio browser console --clear
```

#### `aio browser snapshot`

Get the accessibility tree (interactive elements snapshot).

```bash
aio browser snapshot
```

### Tab Management

#### `aio browser tabs`

List all open tabs.

#### `aio browser tab-new [url]`

Open a new tab, optionally navigating to a URL.

```bash
aio browser tab-new
aio browser tab-new https://google.com
```

#### `aio browser tab-close <index>`

Close a tab by its index.

```bash
aio browser tab-close 1
```

### Wait Conditions

#### `aio browser wait <type>`

Wait for various conditions.

| Option | For Type | Description |
|--------|----------|-------------|
| `--selector <sel>` | selector | CSS selector to wait for |
| `--state <state>` | selector | Element state |
| `--url <url>` | url | URL pattern to match |
| `--timeout <seconds>` | all | Timeout |
| `--expression <js>` | function | JS expression |

```bash
aio browser wait selector --selector ".loaded" --timeout 10
aio browser wait load
aio browser wait url --url "**/dashboard"
aio browser wait network_idle
aio browser wait timeout --timeout 2
aio browser wait function --expression "document.readyState === 'complete'"
```

## Patterns

### Form submission

```bash
aio browser navigate https://app.com/login
aio browser fill "admin" -s "#username"
aio browser fill "pass123" -s "#password"
aio browser click "button[type=submit]"
aio browser wait url --url "**/dashboard"
aio browser screenshot -o dashboard.png
```

### Data extraction

```bash
aio browser navigate https://example.com/api-docs
aio browser wait load
aio browser evaluate "JSON.stringify([...document.querySelectorAll('h2')].map(e => e.textContent))"
```

### Screenshot documentation

```bash
aio browser navigate https://app.com/settings
aio browser wait selector --selector ".settings-panel"
aio browser screenshot --full -o settings-page.png
```
