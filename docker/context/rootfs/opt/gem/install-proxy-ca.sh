#!/bin/bash

set -euo pipefail

log() {
  echo "$(date '+%Y-%m-%d %H:%M:%S,%3N') INFO $*"
}

CERT_PATH="$(printf '%s' "${PROXY_CA_CERT_PATH:-}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
TARGET_DIR="${AIO_PROXY_CA_TARGET_DIR:-/usr/local/share/ca-certificates}"
UPDATE_COMMAND="${AIO_PROXY_CA_UPDATE_COMMAND:-update-ca-certificates}"

if [ -z "${CERT_PATH}" ]; then
  log "PROXY_CA_CERT_PATH not provided (skip extra CA installation)"
  exit 0
fi

SOURCE_PATH="${CERT_PATH}"
TARGET_NAME="custom"
log "Proxy CA explicit path requested: ${SOURCE_PATH}"

if [ ! -f "${SOURCE_PATH}" ]; then
  echo "Proxy CA source file not found: ${SOURCE_PATH}" >&2
  exit 1
fi

TARGET_PATH="${TARGET_DIR}/aio-proxy-ca-${TARGET_NAME}.crt"
mkdir -p "${TARGET_DIR}"
cp "${SOURCE_PATH}" "${TARGET_PATH}"
chmod 644 "${TARGET_PATH}"
log "Installed proxy CA from ${SOURCE_PATH} to ${TARGET_PATH}"

if [ -n "${UPDATE_COMMAND}" ] && [ "${UPDATE_COMMAND}" != ":" ]; then
  ${UPDATE_COMMAND}
  log "Updated system CA certificates"
fi
