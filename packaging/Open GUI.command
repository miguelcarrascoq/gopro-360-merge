#!/bin/bash
# Double-click this file once after unzipping a download from the internet.
# Clears Gatekeeper quarantine on this folder, then starts the GUI.
set -euo pipefail
cd "$(dirname "$0")"
xattr -cr . >/dev/null 2>&1 || true
exec ./gopro-360-gui
