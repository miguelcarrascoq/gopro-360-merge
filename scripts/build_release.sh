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
  arm64|aarch64)
    PLATFORM="macos-arm64"
    MP4_ASSET="mp4_merge-mac-arm64"
    FFMPEG_ASSET="ffmpeg-darwin-arm64"
    FFPROBE_ASSET="ffprobe-darwin-arm64"
    ;;
  x86_64|amd64)
    PLATFORM="macos-x64"
    MP4_ASSET="mp4_merge-mac64"
    FFMPEG_ASSET="ffmpeg-darwin-x64"
    FFPROBE_ASSET="ffprobe-darwin-x64"
    ;;
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

# Static macOS builds (native arm64/x64). evermeet.cx redirects are unreliable.
FFMPEG_BASE="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
FFMPEG_URL="${FFMPEG_BASE}/${FFMPEG_ASSET}.gz"
FFPROBE_URL="${FFMPEG_BASE}/${FFPROBE_ASSET}.gz"
MP4_URL="https://github.com/gyroflow/mp4-merge/releases/download/v0.1.11/${MP4_ASSET}"

echo "Building ${ARTIFACT}"

mkdir -p "$CACHE" "$DIST"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "Creating build venv at ${VENV}"
  python3 -m venv "$VENV"
fi

"${VENV}/bin/python" -m pip install --upgrade pip wheel
"${VENV}/bin/python" -m pip install -e "${ROOT}" pyinstaller

# --- ffmpeg / ffprobe (static builds from eugeneware/ffmpeg-static) ---
FFMPEG_GZ="${CACHE}/${FFMPEG_ASSET}.gz"
FFPROBE_GZ="${CACHE}/${FFPROBE_ASSET}.gz"
FFMPEG_DIR="${CACHE}/ffmpeg-mac"
FFMPEG_BIN="${FFMPEG_DIR}/ffmpeg"
FFPROBE_BIN="${FFMPEG_DIR}/ffprobe"

if [[ ! -f "$FFMPEG_GZ" ]]; then
  echo "Downloading ffmpeg (${FFMPEG_ASSET})..."
  curl -fsSL -o "$FFMPEG_GZ" "$FFMPEG_URL"
fi
if [[ ! -f "$FFPROBE_GZ" ]]; then
  echo "Downloading ffprobe (${FFPROBE_ASSET})..."
  curl -fsSL -o "$FFPROBE_GZ" "$FFPROBE_URL"
fi

mkdir -p "$FFMPEG_DIR"
if [[ ! -f "${FFMPEG_DIR}/.ok" ]]; then
  rm -rf "${FFMPEG_DIR:?}/"*
  gunzip -c "$FFMPEG_GZ" > "$FFMPEG_BIN"
  gunzip -c "$FFPROBE_GZ" > "$FFPROBE_BIN"
  touch "${FFMPEG_DIR}/.ok"
fi

if [[ ! -f "$FFMPEG_BIN" || ! -f "$FFPROBE_BIN" ]]; then
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
