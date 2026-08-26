#!/bin/sh

set -u

runner_pid="$1"
pid_path="$2"
shift 2

supervisor_pid=$$
tunnel_pid=""
watchdog_pid=""
cleanup_started=0

cleanup() {
  if [ "$cleanup_started" -eq 1 ]; then
    return
  fi
  cleanup_started=1
  trap - INT TERM HUP

  if [ -n "$watchdog_pid" ]; then
    kill -TERM "$watchdog_pid" 2>/dev/null || true
    wait "$watchdog_pid" 2>/dev/null || true
  fi
  if [ -n "$tunnel_pid" ]; then
    kill -TERM "$tunnel_pid" 2>/dev/null || true
    attempt=0
    while kill -0 "$tunnel_pid" 2>/dev/null && [ "$attempt" -lt 10 ]; do
      sleep 1
      attempt=$((attempt + 1))
    done
    if kill -0 "$tunnel_pid" 2>/dev/null; then
      kill -KILL "$tunnel_pid" 2>/dev/null || true
    fi
    wait "$tunnel_pid" 2>/dev/null || true
  fi
  rm -f "$pid_path"
}

trap 'cleanup; exit 0' INT TERM HUP

"$@" &
tunnel_pid=$!
printf '%s\n' "$supervisor_pid" > "$pid_path"

(
  while kill -0 "$runner_pid" 2>/dev/null; do
    sleep 1
  done
  kill -TERM "$supervisor_pid" 2>/dev/null || true
) &
watchdog_pid=$!

wait "$tunnel_pid"
status=$?
cleanup
exit "$status"
