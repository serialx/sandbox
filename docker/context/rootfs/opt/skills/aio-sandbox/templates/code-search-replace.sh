#!/bin/bash
# Template: Code Search and Replace Workflow
# Purpose: Find occurrences of a pattern across a codebase and replace them
# Usage: ./code-search-replace.sh <search-pattern> <directory> [file-types...]
#
# This template demonstrates the search-review-replace pattern:
# 1. Grep to find all occurrences
# 2. Review matches with context
# 3. Replace in each file
#
# Customize: Update the OLD_TEXT and NEW_TEXT variables for your refactoring

set -euo pipefail

PATTERN="${1:?Usage: $0 <search-pattern> <directory> [file-types...]}"
DIRECTORY="${2:?Usage: $0 <search-pattern> <directory> [file-types...]}"
shift 2
FILE_TYPES=("$@")

echo "Search pattern: $PATTERN"
echo "Directory: $DIRECTORY"

# Step 1: Find all matches
INCLUDE_ARGS=""
if [ ${#FILE_TYPES[@]} -gt 0 ]; then
  INCLUDE_ARGS="--include ${FILE_TYPES[*]}"
fi

echo ""
echo "=== Matches Found ==="
aio file grep "$PATTERN" "$DIRECTORY" $INCLUDE_ARGS -B 1 -A 1 --output json | \
  jq -r '.matches[] | "\(.file):\(.line_number): \(.line_content)"'

echo ""
MATCH_COUNT=$(aio file grep "$PATTERN" "$DIRECTORY" $INCLUDE_ARGS --output json | jq '.match_count')
echo "Total matches: $MATCH_COUNT"

# Step 2: Review (uncomment to see full context)
# echo ""
# echo "=== Detailed Context ==="
# aio file grep "$PATTERN" "$DIRECTORY" $INCLUDE_ARGS -B 3 -A 3

# Step 3: Replace (uncomment and customize)
# OLD_TEXT="old_function_name"
# NEW_TEXT="new_function_name"
# FILES=$(aio file grep "$PATTERN" "$DIRECTORY" $INCLUDE_ARGS --output json | jq -r '[.matches[].file] | unique[]')
# for file in $FILES; do
#   echo "Replacing in: $file"
#   aio file replace "$file" --old "$OLD_TEXT" --new "$NEW_TEXT"
# done

echo ""
echo "Done. Review matches above, then uncomment the replace section to apply changes."
