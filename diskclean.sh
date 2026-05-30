#!/usr/bin/env bash
# Launch diskclean on macOS or Linux.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if command -v python3 >/dev/null 2>&1; then
    python3 "$DIR/diskclean.py"
else
    python "$DIR/diskclean.py"
fi
