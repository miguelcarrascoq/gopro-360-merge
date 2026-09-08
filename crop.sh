#!/usr/bin/env bash
# Bootstrap deps if needed, confirm GoPro folder, then crop a .360 block.
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
  if [[ ! -x "$VENV/bin/gopro-360-merge" ]] \
    || ! "$PYTHON_BIN" -c "import gopro_360_merge" >/dev/null 2>&1; then
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

confirm_yes() {
  local prompt="$1"
  local reply
  read -r -p "$prompt [Y/n] " reply || true
  reply="${reply:-Y}"
  case "$reply" in
    Y|y|yes|YES) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_directory() {
  local dir="$1"
  if [[ "$dir" == "~"* ]]; then
    dir="${dir/#\~/$HOME}"
  fi
  if [[ -d "$dir" ]]; then
    (cd "$dir" && pwd)
  else
    echo "$dir"
  fi
}

DIR_ARG=""
FLAGS=()
if (($# > 0)) && [[ "${1:-}" != -* ]]; then
  DIR_ARG="$1"
  shift
fi
FLAGS=("$@")

require_python
ensure_venv
ensure_package
ensure_ffmpeg

echo
if [[ -n "$DIR_ARG" ]]; then
  DIR="$(resolve_directory "$DIR_ARG")"
  echo "Carpeta: $DIR"
  if ! confirm_yes "Usar esta carpeta?"; then
    echo "Cancelado."
    exit 0
  fi
else
  read -r -p "Carpeta con archivos GS*.360 originales [.]: " input || true
  input="${input:-.}"
  DIR="$(resolve_directory "$input")"
  echo "Carpeta: $DIR"
  if ! confirm_yes "Usar esta carpeta?"; then
    echo "Cancelado."
    exit 0
  fi
fi

if [[ ! -d "$DIR" ]]; then
  die "Not a directory: $DIR"
fi

echo
exec "$VENV/bin/gopro-360-merge" crop "$DIR" "${FLAGS[@]}"
