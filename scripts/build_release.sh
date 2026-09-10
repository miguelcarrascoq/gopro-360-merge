#!/usr/bin/env bash
# Build a self-contained macOS release zip for GitHub Releases.
# Run on a Mac (not Windows):  ./scripts/build_release.sh
#
# Notes for a later machine:
# - Produces dist/gopro-360-merge-<ver>-macos-arm64.zip or …-macos-x64.zip
# - Code signing / notarization are out of scope here (Gatekeeper may block first run)
# - The repo Gopro360Merge.app is a development Dock launcher, not this release artifact

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

get_version() {
  python3 -c "import pathlib,re; t=pathlib.Path('src/gopro_360_merge/__init__.py').read_text(); print(re.search(r'__version__\\s*=\\s*\"([^\"]+)\"', t).group(1))"
}

VERSION="$(get_version)"
ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  arm64|aarch64) PLATFORM="macos-arm64"; MP4_ASSET="mp4_merge-mac-arm64" ;;
  x86_64|amd64)  PLATFORM="macos-x64";   MP4_ASSET="mp4_merge-mac64" ;;
  *) echo "Unsupported arch: $ARCH_RAW" >&2; exit 1 ;;
esac

ARTIFACT="gopro-360-merge-${VERSION}-${PLATFORM}"
CACHE="${ROOT}/.release-cache"
VENV="${ROOT}/.venv-release"
DIST="${ROOT}/dist"
BUILD="${ROOT}/build"
STAGING="${DIST}/${ARTIFACT}"
ZIP="${DIST}/${ARTIFACT}.zip"
PYI_OUT="${DIST}/gopro360merge"

FFMPEG_URL="https://evermeet.cx/ffmpeg/getrelease/ffmpeg/zip"
FFPROBE_URL="https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip"
MP4_URL="https://github.com/gyroflow/mp4-merge/releases/download/v0.1.11/${MP4_ASSET}"

echo "Building ${ARTIFACT}"

mkdir -p "$CACHE" "$DIST"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "Creating build venv at ${VENV}"
  python3 -m venv "$VENV"
fi

"${VENV}/bin/python" -m pip install --upgrade pip wheel
"${VENV}/bin/python" -m pip install -e "${ROOT}" pyinstaller

# --- ffmpeg / ffprobe (static builds from evermeet.cx) ---
FFMPEG_ZIP="${CACHE}/ffmpeg-mac.zip"
FFPROBE_ZIP="${CACHE}/ffprobe-mac.zip"
if [[ ! -f "$FFMPEG_ZIP" ]]; then
  echo "Downloading ffmpeg..."
  curl -fsSL -o "$FFMPEG_ZIP" "$FFMPEG_URL"
fi
if [[ ! -f "$FFPROBE_ZIP" ]]; then
  echo "Downloading ffprobe..."
  curl -fsSL -o "$FFPROBE_ZIP" "$FFPROBE_URL"
fi

FFMPEG_DIR="${CACHE}/ffmpeg-mac"
mkdir -p "$FFMPEG_DIR"
if [[ ! -f "${FFMPEG_DIR}/.ok" ]]; then
  rm -rf "${FFMPEG_DIR:?}/"*
  unzip -o -q "$FFMPEG_ZIP" -d "$FFMPEG_DIR"
  unzip -o -q "$FFPROBE_ZIP" -d "$FFMPEG_DIR"
  touch "${FFMPEG_DIR}/.ok"
fi

FFMPEG_BIN="$(find "$FFMPEG_DIR" -type f -name ffmpeg | head -n 1)"
FFPROBE_BIN="$(find "$FFMPEG_DIR" -type f -name ffprobe | head -n 1)"
if [[ -z "$FFMPEG_BIN" || -z "$FFPROBE_BIN" ]]; then
  echo "ffmpeg/ffprobe not found after extract" >&2
  exit 1
fi
chmod +x "$FFMPEG_BIN" "$FFPROBE_BIN"

# --- mp4-merge ---
MP4_CACHED="${CACHE}/mp4_merge"
if [[ ! -f "$MP4_CACHED" ]]; then
  echo "Downloading mp4-merge (${MP4_ASSET})..."
  curl -fsSL -o "$MP4_CACHED" "$MP4_URL"
fi
chmod +x "$MP4_CACHED"

# --- PyInstaller ---
echo "Running PyInstaller..."
rm -rf "$PYI_OUT"
"${VENV}/bin/python" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST" \
  --workpath "$BUILD" \
  "${ROOT}/packaging/gopro360merge.spec"

if [[ ! -d "$PYI_OUT" ]]; then
  echo "PyInstaller output missing: $PYI_OUT" >&2
  exit 1
fi

# --- Stage ---
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "${PYI_OUT}/." "$STAGING/"

TOOLS="${STAGING}/tools"
mkdir -p "$TOOLS"
cp "$FFMPEG_BIN" "${TOOLS}/ffmpeg"
cp "$FFPROBE_BIN" "${TOOLS}/ffprobe"
cp "$MP4_CACHED" "${TOOLS}/mp4_merge"
chmod +x "${TOOLS}/ffmpeg" "${TOOLS}/ffprobe" "${TOOLS}/mp4_merge"

cp "${ROOT}/LICENSE" "${STAGING}/LICENSE"
cp "${ROOT}/src/gopro_360_merge/vendor/NOTICE" "${STAGING}/NOTICE"
cp "${ROOT}/packaging/RELEASE_README.txt" "${STAGING}/README.txt"

rm -f "$ZIP"
(
  cd "$DIST"
  zip -r -q "$(basename "$ZIP")" "$(basename "$STAGING")"
)

echo
echo "Done."
echo "  Folder: $STAGING"
echo "  Zip:    $ZIP"
echo
echo "Publish (when ready):"
echo "  git tag v${VERSION}"
echo "  git push origin v${VERSION}"
echo "  gh release create v${VERSION} \"$ZIP\" --title \"v${VERSION}\" --notes-file CHANGELOG.md"
