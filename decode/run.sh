#!/usr/bin/env bash
set -euo pipefail
umask 077
# Per-user lock in runtime directory: held across every trial and watchdog.
LOCKDIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
[[ -d "$LOCKDIR" && -w "$LOCKDIR" ]] || { echo 'A writable private XDG_RUNTIME_DIR is required' >&2; exit 2; }
exec 9>"$LOCKDIR/hevc-industrial.lock"
if ! flock -n 9; then
  echo 'Another HEVC test is running; lock owner:' >&2
  if command -v fuser >/dev/null 2>&1; then
    fuser -v "$LOCKDIR/hevc-industrial.lock" >&2 || true
  else
    echo "Inspect processes with: ps -ef | grep '[b]ench.py'" >&2
  fi
  exit 2
fi
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec /usr/bin/python3 bench.py "$@"
