#!/bin/bash

set -e

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S,%3N') INFO $@"
}

sleep 3

log "Starting Jupyter Lab..."
exec /opt/python3.12/bin/jupyter lab \
  --notebook-dir=${WORKSPACE:-/tmp} \
  --ip=127.0.0.1 \
  --port=${JUPYTER_LAB_PORT} \
  --no-browser \
  --allow-root \
  --ServerApp.token='' \
  --ServerApp.password='' \
  --ServerApp.allow_remote_access=True \
  --ServerApp.allow_origin='*' \
  --ServerApp.disable_check_xsrf=True \
  --ServerApp.base_url='/jupyter' \
  --ServerApp.tornado_settings='{"headers":{"Access-Control-Allow-Origin":"*","Content-Security-Policy":"frame-ancestors *"}}'
