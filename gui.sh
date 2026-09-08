#!/usr/bin/env bash
# Bootstrap deps if needed, then open the gopro-360-merge desktop GUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/.venv"
PYTHON_BIN=""

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "→ $*"
}

require_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    die "Python 3.11+ is required. Install Python and retry."
  fi
  local ver
  ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  local major minor
  IFS=. read -r major minor <<<"$ver"
  if (( major < 3 || (major == 3 && minor < 11) )); then
    die "Python 3.11+ is required (found $ver)."
  fi
  PYTHON_BIN="$(command -v python3)"
}

ensure_venv() {
  if [[ ! -x "$VENV/bin/python" ]]; then
    info "Creating virtualenv at .venv"
    "$PYTHON_BIN" -m venv "$VENV"
  fi
  PYTHON_BIN="$VENV/bin/python"
}

ensure_package() {
  if [[ ! -x "$VENV/bin/gopro-360-gui" ]] \
    || ! "$PYTHON_BIN" -c "import gopro_360_merge, customtkinter" >/dev/null 2>&1; then
    info "Installing gopro-360-merge into .venv"
    "$PYTHON_BIN" -m pip install -q -e .
  fi
}

ensure_ffmpeg() {
  local missing=()
  command -v ffmpeg >/dev/null 2>&1 || missing+=(ffmpeg)
  command -v ffprobe >/dev/null 2>&1 || missing+=(ffprobe)
  if ((${#missing[@]} == 0)); then
    return 0
  fi

  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    info "Installing ffmpeg via Homebrew (missing: ${missing[*]})"
    brew install ffmpeg
    return 0
  fi

  die "Missing required tools: ${missing[*]}. Install ffmpeg (includes ffprobe), e.g. brew install ffmpeg"
}

# Linux: install a .desktop launcher with absolute Icon/Exec for the app menu.
ensure_linux_desktop() {
  [[ "$(uname -s)" == "Linux" ]] || return 0
  local icon="$SCRIPT_DIR/src/gopro_360_merge/assets/app_icon.png"
  local template="$SCRIPT_DIR/gopro-360-gui.desktop"
  local dest="${XDG_DATA_HOME:-$HOME/.local/share}/applications/gopro-360-gui.desktop"
  [[ -f "$icon" && -f "$template" ]] || return 0
  mkdir -p "$(dirname "$dest")"
  sed \
    -e "s|PLACEHOLDER_EXEC|$SCRIPT_DIR/gui.sh|" \
    -e "s|PLACEHOLDER_ICON|$icon|" \
    -e "s|PLACEHOLDER_PATH|$SCRIPT_DIR|" \
    "$template" >"$dest"
  chmod +x "$dest" 2>/dev/null || true
}

require_python
ensure_venv
ensure_package
ensure_ffmpeg
ensure_linux_desktop

echo
info "Opening GUI…"
exec "$VENV/bin/gopro-360-gui"
