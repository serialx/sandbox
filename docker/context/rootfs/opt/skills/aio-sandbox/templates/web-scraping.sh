#!/bin/bash
# Template: Web Scraping Workflow
# Purpose: Navigate to a page, extract content, save results
# Usage: ./web-scraping.sh <url> [output-dir]
#
# Outputs:
#   - page.png: Full page screenshot
#   - content.txt: Extracted text
#   - content.md: Markdown conversion
#   - page.html: Raw HTML

set -euo pipefail

TARGET_URL="${1:?Usage: $0 <url> [output-dir]}"
OUTPUT_DIR="${2:-.}"

echo "Scraping: $TARGET_URL"
mkdir -p "$OUTPUT_DIR"

# Step 1: Navigate and wait for page load
aio browser navigate "$TARGET_URL" --wait-until networkidle

# Step 2: Capture screenshot
aio browser screenshot --full -o "$OUTPUT_DIR/page.png"
echo "Saved: $OUTPUT_DIR/page.png"

# Step 3: Extract text content
aio browser text > "$OUTPUT_DIR/content.txt"
echo "Saved: $OUTPUT_DIR/content.txt"

# Step 4: Get markdown (best for LLM processing)
aio browser markdown > "$OUTPUT_DIR/content.md"
echo "Saved: $OUTPUT_DIR/content.md"

# Step 5: Save raw HTML
aio browser html > "$OUTPUT_DIR/page.html"
echo "Saved: $OUTPUT_DIR/page.html"

# Step 6: Get page metadata
echo ""
echo "=== Page Info ==="
aio browser evaluate "JSON.stringify({title: document.title, url: window.location.href, links: document.querySelectorAll('a').length, images: document.querySelectorAll('img').length})" --output json

# Optional: Handle infinite scroll
# echo "Scrolling to load more content..."
# for i in {1..5}; do
#   aio browser scroll --dir down --amt 1000
#   sleep 1
# done
# aio browser screenshot --full -o "$OUTPUT_DIR/page-scrolled.png"
# aio browser text > "$OUTPUT_DIR/content-full.txt"

echo ""
echo "Scraping complete:"
ls -la "$OUTPUT_DIR"
