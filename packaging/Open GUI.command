#!/bin/bash
# Double-click after unzipping. Clears quarantine (fallback) and opens the app.
set -euo pipefail
cd "$(dirname "$0")"
xattr -cr . >/dev/null 2>&1 || true
if [[ -d "./Gopro360Merge.app" ]]; then
  open "./Gopro360Merge.app"
else
  exec ./gopro-360-gui
fi
