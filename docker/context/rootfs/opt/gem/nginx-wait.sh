#!/bin/bash

trim_wait_item() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

join_wait_items() {
  local first=1
  local item
  for item in "$@"; do
    if [ $first -eq 1 ]; then
      printf '%s' "$item"
      first=0
    else
      printf ',%s' "$item"
    fi
  done
}

# Declare the arrays that will hold the final readiness targets to check.
PORTS_ARRAY=()
FILES_ARRAY=()

# The logic forks here:
# 1. If WAIT_PORTS is provided, we process it as a string override.
# 2. If WAIT_PORTS is not provided, we build the array directly from individual variables.
if [ -n "$WAIT_PORTS" ]; then
  # Case 1: WAIT_PORTS is set, so it overrides the individual variables.

  # If /opt/gem/mcp.disabled does exist, remove port 8089 from the wait list.
  # This is done after setting WAIT_PORTS to handle both default and environment-provided values.
  if [ -f /opt/gem/mcp.disabled ]; then
    if [ -n "$MCP_SERVER_PORT" ]; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') - /opt/gem/mcp.disabled found, removing port $MCP_SERVER_PORT from wait list."
      # Use sed to robustly remove 8089 and its surrounding commas if they exist
      # 1. Remove '8089,' (for start/middle position)
      # 2. Remove ',8089' (for end position)
      # 3. Remove '8089' (if it's the only port in the list)
      WAIT_PORTS=$(echo "$WAIT_PORTS" | sed -e "s/$MCP_SERVER_PORT,//g" -e "s/,$MCP_SERVER_PORT//g" -e "s/^$MCP_SERVER_PORT$//g")
    fi
  fi

  # Convert the final, processed string to an array.
  IFS=',' read -ra PORTS_ARRAY <<<"$WAIT_PORTS"
else
  # Case 2: WAIT_PORTS is not set. Build the array directly.

  # Add ports to the array one by one, checking conditions as we go.
  [ -n "$SANDBOX_SRV_PORT" ] && PORTS_ARRAY+=("$SANDBOX_SRV_PORT")
  [ -n "$BROWSER_REMOTE_DEBUGGING_PORT" ] && PORTS_ARRAY+=("$BROWSER_REMOTE_DEBUGGING_PORT")
fi

if [ -n "$WAIT_FILES" ]; then
  IFS=',' read -ra FILES_ARRAY <<<"$WAIT_FILES"
fi

normalized_ports=()
for port in "${PORTS_ARRAY[@]}"; do
  port=$(trim_wait_item "$port")
  [ -n "$port" ] && normalized_ports+=("$port")
done
PORTS_ARRAY=("${normalized_ports[@]}")

normalized_files=()
for file in "${FILES_ARRAY[@]}"; do
  file=$(trim_wait_item "$file")
  [ -n "$file" ] && normalized_files+=("$file")
done
FILES_ARRAY=("${normalized_files[@]}")

# Proceed only if there are readiness targets in the final arrays.
if [ ${#PORTS_ARRAY[@]} -eq 0 ] && [ ${#FILES_ARRAY[@]} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - No ports or files to wait for. Proceeding directly."
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - Waiting for services to be ready..."
  [ ${#PORTS_ARRAY[@]} -gt 0 ] && echo "Ports to check: $(join_wait_items "${PORTS_ARRAY[@]}")"
  [ ${#FILES_ARRAY[@]} -gt 0 ] && echo "Files to check: $(join_wait_items "${FILES_ARRAY[@]}")"
  echo "Timeout: ${WAIT_TIMEOUT}s, Check interval: ${WAIT_INTERVAL}s"

  # Record start time
  start_time=$(date +%s)
  # Track which readiness targets are already ready (for timing)
  declare -A port_ready_time
  declare -A file_ready_time

  # Track pending readiness targets
  declare -A port_pending
  declare -A file_pending
  for port in "${PORTS_ARRAY[@]}"; do
    [ -n "$port" ] && port_pending[$port]=1
  done
  for file in "${FILES_ARRAY[@]}"; do
    [ -n "$file" ] && file_pending["$file"]=1
  done

  # Main loop - check ALL readiness targets each iteration (parallel style)
  while true; do
    pending_ports=()
    pending_files=()

    for port in "${!port_pending[@]}"; do
      if nc -z localhost "$port" >/dev/null 2>&1; then
        # Port is ready - record time
        current_time=$(date +%s)
        elapsed=$((current_time - start_time))
        port_ready_time[$port]=$elapsed
        unset port_pending[$port]
      else
        pending_ports+=("$port")
      fi
    done

    for file in "${!file_pending[@]}"; do
      if [ -f "$file" ]; then
        current_time=$(date +%s)
        elapsed=$((current_time - start_time))
        file_ready_time["$file"]=$elapsed
        unset file_pending["$file"]
      else
        pending_files+=("$file")
      fi
    done

    # Log pending targets (only once per second to reduce noise)
    if [ ${#pending_ports[@]} -gt 0 ] || [ ${#pending_files[@]} -gt 0 ]; then
      current_time=$(date +%s)
      if [ "$current_time" != "${last_log_time:-}" ]; then
        [ ${#pending_ports[@]} -gt 0 ] && echo "$(date '+%Y-%m-%d %H:%M:%S') - Waiting for ports: $(join_wait_items "${pending_ports[@]}")"
        [ ${#pending_files[@]} -gt 0 ] && echo "$(date '+%Y-%m-%d %H:%M:%S') - Waiting for files: $(join_wait_items "${pending_files[@]}")"
        last_log_time=$current_time
      fi
    fi

    # Check if all readiness targets are ready
    if [ ${#port_pending[@]} -eq 0 ] && [ ${#file_pending[@]} -eq 0 ]; then
      echo ""
      echo "$(date '+%Y-%m-%d %H:%M:%S') - All services are ready!"
      echo ""
      # Print summary sorted by ready time
      echo "=== Startup Time Summary (sorted by speed) ==="
      {
        for port in "${!port_ready_time[@]}"; do
          printf "%s\tport\t%s\n" "${port_ready_time[$port]}" "$port"
        done
        for file in "${!file_ready_time[@]}"; do
          printf "%s\tfile\t%s\n" "${file_ready_time[$file]}" "$file"
        done
      } | sort -n | while IFS=$'\t' read -r time target_type target; do
        if [ "$target_type" = "port" ]; then
          printf "  Port %s: %ds\n" "$target" "$time"
        else
          printf "  File %s: %ds\n" "$target" "$time"
        fi
      done
      echo "==============================================="
      echo ""

      break
    fi

    # Check timeout
    current_time=$(date +%s)
    elapsed=$((current_time - start_time))
    if [ $elapsed -ge $WAIT_TIMEOUT ]; then
      echo "$(date '+%Y-%m-%d %H:%M:%S') - Timeout after ${WAIT_TIMEOUT}s waiting for services"
      [ ${#pending_ports[@]} -gt 0 ] && echo "Still waiting for ports: $(join_wait_items "${pending_ports[@]}")"
      [ ${#pending_files[@]} -gt 0 ] && echo "Still waiting for files: $(join_wait_items "${pending_files[@]}")"
      exit 1
    fi

    sleep $WAIT_INTERVAL
  done
fi

# Start nginx
echo "$(date '+%Y-%m-%d %H:%M:%S') - Starting nginx..."
exec /usr/sbin/nginx -c /opt/gem/nginx.conf -g 'daemon off;'
