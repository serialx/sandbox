#!/bin/bash
# Template: Development Environment Setup
# Purpose: Set up a project in the sandbox for development
# Usage: ./dev-setup.sh [workspace-dir]
#
# Steps:
#   1. Get sandbox info
#   2. Explore project structure
#   3. Install dependencies
#   4. Run tests to verify setup
#
# Customize: Update the dependency install and test commands for your project

set -euo pipefail

WORKSPACE="${1:-/workspace}"

echo "=== Sandbox Environment ==="
aio sandbox info

echo ""
echo "=== Project Structure ==="
aio file list "$WORKSPACE" --recursive --depth 2

echo ""
echo "=== Detecting Project Type ==="

# Check for Python project
if aio file glob "requirements.txt" "$WORKSPACE" --output json 2>/dev/null | jq -e '.total_count > 0' > /dev/null 2>&1; then
  echo "Python project detected (requirements.txt)"
  echo "Installing dependencies..."
  aio bash "pip install -r $WORKSPACE/requirements.txt" --timeout 120
  echo ""
  echo "Running tests..."
  aio bash "cd $WORKSPACE && python -m pytest tests/ -v" --timeout 300 || echo "Tests failed or not found"

elif aio file glob "pyproject.toml" "$WORKSPACE" --output json 2>/dev/null | jq -e '.total_count > 0' > /dev/null 2>&1; then
  echo "Python project detected (pyproject.toml)"
  echo "Installing dependencies..."
  aio bash "cd $WORKSPACE && pip install -e ." --timeout 120
  echo ""
  echo "Running tests..."
  aio bash "cd $WORKSPACE && python -m pytest" --timeout 300 || echo "Tests failed or not found"

elif aio file glob "package.json" "$WORKSPACE" --output json 2>/dev/null | jq -e '.total_count > 0' > /dev/null 2>&1; then
  echo "Node.js project detected (package.json)"
  echo "Installing dependencies..."
  aio bash "cd $WORKSPACE && npm install" --timeout 120
  echo ""
  echo "Running tests..."
  aio bash "cd $WORKSPACE && npm test" --timeout 300 || echo "Tests failed or not found"

elif aio file glob "go.mod" "$WORKSPACE" --output json 2>/dev/null | jq -e '.total_count > 0' > /dev/null 2>&1; then
  echo "Go project detected (go.mod)"
  echo "Installing dependencies..."
  aio bash "cd $WORKSPACE && go mod download" --timeout 120
  echo ""
  echo "Running tests..."
  aio bash "cd $WORKSPACE && go test ./..." --timeout 300 || echo "Tests failed or not found"

else
  echo "No recognized project type found. Manual setup required."
  echo ""
  echo "Available packages:"
  aio sandbox packages --python
fi

echo ""
echo "=== Setup Complete ==="
echo "Workspace: $WORKSPACE"
