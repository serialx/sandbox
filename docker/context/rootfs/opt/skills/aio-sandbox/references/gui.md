# GUI Commands Reference

## Overview

Low-level GUI actions using pyautogui backend. Controls the desktop display directly — mouse clicks, keyboard input, and screenshots at the OS level.

**GUI vs Browser:** Use `aio gui` for desktop-level interactions (coordinates-based). Use `aio browser` for web page interactions (selector-based, Playwright).

## Commands

### `aio gui screenshot`

Take a screenshot of the entire desktop.

| Option | Description | Default |
|--------|-------------|---------|
| `-o, --output <file>` | Output file path | screenshot.png |

```bash
aio gui screenshot
aio gui screenshot -o desktop.png
```

### `aio gui tap <x> <y>`

Click at screen coordinates.

```bash
aio gui tap 500 300
aio gui tap 100 200
```

### `aio gui double-click [x] [y]`

Double-click at coordinates (or current cursor position).

```bash
aio gui double-click 500 300
aio gui double-click
```

### `aio gui right-click [x] [y]`

Right-click at coordinates (or current cursor position).

```bash
aio gui right-click 500 300
aio gui right-click
```

### `aio gui move <x> <y>`

Move mouse cursor to coordinates.

```bash
aio gui move 200 400
```

### `aio gui drag <x> <y>`

Drag from current position to target coordinates.

```bash
aio gui drag 800 600
```

### `aio gui scroll`

Scroll the screen.

| Option | Description | Default |
|--------|-------------|---------|
| `--dx <n>` | Horizontal scroll amount | 0 |
| `--dy <n>` | Vertical scroll amount | 0 |

```bash
aio gui scroll --dy -300    # Scroll up
aio gui scroll --dy 300     # Scroll down
aio gui scroll --dx 100     # Scroll right
```

### `aio gui type <text>`

Type text on the keyboard.

```bash
aio gui type "Hello World"
```

### `aio gui press <key>`

Press a single key.

```bash
aio gui press Enter
aio gui press Tab
aio gui press Escape
```

### `aio gui hotkey <keys...>`

Press a key combination.

```bash
aio gui hotkey ctrl c
aio gui hotkey ctrl shift s
aio gui hotkey alt F4
```

### `aio gui wait <seconds>`

Wait for a duration.

```bash
aio gui wait 2
aio gui wait 0.5
```

### `aio gui info`

Get browser/screen information (viewport size, CDP URL).

```bash
aio gui info
```

## Patterns

### Desktop application interaction

```bash
aio gui screenshot -o before.png
aio gui tap 100 50          # Click menu
aio gui wait 0.5
aio gui tap 120 80          # Click menu item
aio gui wait 1
aio gui screenshot -o after.png
```

### Keyboard-driven workflow

```bash
aio gui hotkey ctrl l        # Focus address bar
aio gui type "https://example.com"
aio gui press Enter
aio gui wait 2
aio gui screenshot
```
