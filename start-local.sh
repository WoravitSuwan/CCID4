#!/bin/sh
# Summons system - start on this computer (no Docker needed). macOS / Linux.
#   ./start-local.sh        ./start-local.sh demo
cd "$(dirname "$0")" || exit 1
for py in python3.12 python3.13 python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    exec "$py" tools/run_local.py "$@"
  fi
done
echo "Python 3.11+ not found. Install it from https://www.python.org/downloads/" >&2
exit 1
