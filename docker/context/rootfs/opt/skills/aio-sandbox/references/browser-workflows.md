# Browser Workflows

End-to-end patterns for common browser automation tasks.

**Related**: [browser.md](browser.md) for full command reference, [SKILL.md](../SKILL.md) for quick start.

## Contents

- [Form Submission](#form-submission)
- [Web Scraping](#web-scraping)
- [Screenshot Documentation](#screenshot-documentation)
- [SPA Navigation](#spa-navigation)
- [Error Handling](#error-handling)
- [Content Extraction Pipeline](#content-extraction-pipeline)
- [Testing Workflows](#testing-workflows)

## Form Submission

```bash
# Navigate to the form page
aio browser navigate https://app.example.com/signup

# Wait for the form to load
aio browser wait selector --selector "form"

# Get a snapshot to discover form elements
aio browser snapshot

# Fill fields using CSS selectors
aio browser fill "Jane Doe" -s "#name"
aio browser fill "jane@example.com" -s "#email"
aio browser fill "SecureP@ss123" -s "input[type=password]"

# Submit the form
aio browser click "button[type=submit]"

# Wait for result
aio browser wait load
aio browser screenshot -o form-result.png
```

## Web Scraping

### Single Page Extraction

```bash
aio browser navigate https://example.com/products
aio browser wait load

# Get all text
aio browser text

# Get structured content
aio browser evaluate "JSON.stringify([...document.querySelectorAll('.product')].map(p => ({name: p.querySelector('h2').textContent, price: p.querySelector('.price').textContent})))"

# Get as markdown (useful for LLM processing)
aio browser markdown
```

### Multi-Page Scraping

```bash
#!/bin/bash
for page in 1 2 3 4 5; do
  aio browser navigate "https://example.com/list?page=$page"
  aio browser wait load
  aio browser text >> all-pages.txt
  echo "--- Page $page ---" >> all-pages.txt
done
```

### Infinite Scroll

```bash
aio browser navigate https://example.com/feed
aio browser wait load

# Scroll to load more content
for i in {1..5}; do
  aio browser scroll --dir down --amt 1000
  aio browser wait selector --selector ".loading" --timeout 5 2>/dev/null || true
  sleep 1
done

# Capture everything
aio browser screenshot --full -o feed-complete.png
aio browser text > feed-content.txt
```

## Screenshot Documentation

### Step-by-Step Screenshots

```bash
#!/bin/bash
OUTPUT_DIR="./screenshots"
mkdir -p "$OUTPUT_DIR"

# Step 1: Landing page
aio browser navigate https://app.example.com
aio browser wait load
aio browser screenshot -o "$OUTPUT_DIR/01-landing.png"

# Step 2: Login
aio browser click "a[href='/login']"
aio browser wait load
aio browser screenshot -o "$OUTPUT_DIR/02-login-page.png"

# Step 3: Fill credentials
aio browser fill "demo@example.com" -s "#email"
aio browser fill "demo123" -s "#password"
aio browser screenshot -o "$OUTPUT_DIR/03-form-filled.png"

# Step 4: Dashboard
aio browser click "button[type=submit]"
aio browser wait load
aio browser screenshot --full -o "$OUTPUT_DIR/04-dashboard.png"

echo "Screenshots saved to $OUTPUT_DIR"
```

### Before/After Comparison

```bash
# Capture before
aio browser navigate https://app.example.com/settings
aio browser wait load
aio browser screenshot -o before.png

# Make changes
aio browser click "#dark-mode-toggle"
aio browser wait selector --selector ".dark-theme"

# Capture after
aio browser screenshot -o after.png
```

## SPA Navigation

Single-page apps don't trigger full page loads. Use appropriate wait strategies:

```bash
# Navigate to SPA
aio browser navigate https://spa-app.example.com

# Click navigation (doesn't trigger full load)
aio browser click "a[href='/dashboard']"

# Wait for content to render (NOT page load)
aio browser wait selector --selector ".dashboard-content" --timeout 10

# Or wait for specific URL change
aio browser wait url --url "**/dashboard"

# Or wait for network to settle
aio browser wait network_idle
```

## Error Handling

### Check for Error States

```bash
#!/bin/bash
aio browser navigate https://app.example.com/action
aio browser wait load

# Check if we got an error page
URL=$(aio browser evaluate "window.location.href" --output json 2>/dev/null)
PAGE_TEXT=$(aio browser text 2>/dev/null)

if echo "$PAGE_TEXT" | grep -qi "error\|not found\|403\|500"; then
  echo "Error detected on page"
  aio browser screenshot -o error-page.png
  exit 1
fi
```

### Retry Pattern

```bash
#!/bin/bash
MAX_RETRIES=3
for attempt in $(seq 1 $MAX_RETRIES); do
  aio browser navigate https://flaky-site.example.com
  aio browser wait load

  if aio browser wait selector --selector ".content" --timeout 10 2>/dev/null; then
    echo "Page loaded successfully"
    break
  fi

  echo "Attempt $attempt failed, retrying..."
  if [ "$attempt" -eq "$MAX_RETRIES" ]; then
    echo "All retries exhausted"
    exit 1
  fi
  sleep 2
done
```

## Content Extraction Pipeline

Complete workflow for extracting and processing web content:

```bash
#!/bin/bash
URL="${1:?Usage: $0 <url>}"
OUTPUT_DIR="${2:-.}"
mkdir -p "$OUTPUT_DIR"

# Navigate
aio browser navigate "$URL"
aio browser wait load

# Extract metadata
TITLE=$(aio browser evaluate "document.title")
echo "Title: $TITLE"

# Full page screenshot
aio browser screenshot --full -o "$OUTPUT_DIR/page.png"

# Text content
aio browser text > "$OUTPUT_DIR/content.txt"

# Markdown content (best for LLM processing)
aio browser markdown > "$OUTPUT_DIR/content.md"

# Page HTML
aio browser html > "$OUTPUT_DIR/page.html"

# Console logs (for debugging)
aio browser console > "$OUTPUT_DIR/console.log"

echo "Extracted to $OUTPUT_DIR:"
ls -la "$OUTPUT_DIR"
```

## Testing Workflows

### Smoke Test

```bash
#!/bin/bash
# Quick check that key pages load correctly
URLS=(
  "https://app.example.com/"
  "https://app.example.com/login"
  "https://app.example.com/docs"
)

FAILED=0
for url in "${URLS[@]}"; do
  aio browser navigate "$url" --timeout 15
  aio browser wait load

  TEXT=$(aio browser text 2>/dev/null)
  if [ -z "$TEXT" ]; then
    echo "FAIL: $url (empty page)"
    FAILED=$((FAILED + 1))
  else
    echo "PASS: $url"
  fi
done

echo ""
echo "Results: $((${#URLS[@]} - FAILED))/${#URLS[@]} passed"
exit $FAILED
```

### Visual Regression

```bash
#!/bin/bash
# Capture current state for visual comparison
PAGES=("/" "/login" "/dashboard" "/settings")
BRANCH=$(git rev-parse --short HEAD)

for page in "${PAGES[@]}"; do
  slug=$(echo "$page" | tr '/' '-' | sed 's/^-//')
  [ -z "$slug" ] && slug="home"

  aio browser navigate "https://app.example.com$page"
  aio browser wait load
  aio browser screenshot --full -o "./visual-regression/${BRANCH}-${slug}.png"
done
```
