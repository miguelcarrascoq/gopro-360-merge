#!/usr/bin/env bash
# Build a self-contained macOS release zip for GitHub Releases.
# Run on a Mac:  ./scripts/build_release.sh
#
# Produces dist/gopro-360-merge-<ver>-macos-arm64.zip (or …-macos-x64.zip) with:
#   Gopro360Merge.app  (GUI + shared _internal + tools)
#   gopro-360-merge    (CLI launcher into the .app)
#
# If Developer ID Application + App Store Connect API env vars are set, signs
# with hardened runtime, notarizes via notarytool, and staples the .app.
# Otherwise falls back to ad-hoc codesign (Gatekeeper may still require xattr).
#
# Env (for notarized builds):
#   CODESIGN_IDENTITY          default: auto-detect Developer ID Application
#   APP_STORE_CONNECT_KEY_ID
#   APP_STORE_CONNECT_ISSUER_ID
#   APP_STORE_CONNECT_KEY_PATH

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
APP="${STAGING}/Gopro360Merge.app"
APP_MACOS="${APP}/Contents/MacOS"
APP_RES="${APP}/Contents/Resources/app"
ENTITLEMENTS="${ROOT}/packaging/macos/entitlements.plist"

FFMPEG_BASE="https://github.com/eugeneware/ffmpeg-static/releases/download/b6.1.1"
FFMPEG_URL="${FFMPEG_BASE}/${FFMPEG_ASSET}.gz"
FFPROBE_URL="${FFMPEG_BASE}/${FFPROBE_ASSET}.gz"
MP4_URL="https://github.com/gyroflow/mp4-merge/releases/download/v0.1.11/${MP4_ASSET}"

detect_codesign_identity() {
  if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    echo "$CODESIGN_IDENTITY"
    return
  fi
  security find-identity -v -p codesigning 2>/dev/null \
    | sed -n 's/.*"\(Developer ID Application: .*\)".*/\1/p' \
    | head -n 1
}

CODESIGN_IDENTITY="$(detect_codesign_identity || true)"
CAN_NOTARIZE=0
if [[ -n "${CODESIGN_IDENTITY}" \
   && -n "${APP_STORE_CONNECT_KEY_ID:-}" \
   && -n "${APP_STORE_CONNECT_ISSUER_ID:-}" \
   && -n "${APP_STORE_CONNECT_KEY_PATH:-}" \
   && -f "${APP_STORE_CONNECT_KEY_PATH}" ]]; then
  CAN_NOTARIZE=1
fi

echo "Building ${ARTIFACT}"
if [[ -n "${CODESIGN_IDENTITY}" ]]; then
  echo "  Codesign: ${CODESIGN_IDENTITY}"
else
  echo "  Codesign: ad-hoc (no Developer ID Application found)"
fi
if [[ "$CAN_NOTARIZE" -eq 1 ]]; then
  echo "  Notarize: yes"
else
  echo "  Notarize: no (set APP_STORE_CONNECT_* env vars for notarization)"
fi

mkdir -p "$CACHE" "$DIST"

if [[ ! -x "${VENV}/bin/python" ]]; then
  echo "Creating build venv at ${VENV}"
  python3 -m venv "$VENV"
fi

"${VENV}/bin/python" -m pip install --upgrade pip wheel
"${VENV}/bin/python" -m pip install -e "${ROOT}" pyinstaller

# --- ffmpeg / ffprobe ---
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

# --- Stage as .app (onedir lives in Resources; MacOS only has a launcher) ---
# Apple requires Contents/MacOS to contain executables only; data under MacOS
# breaks Developer ID codesign ("code object is not signed" on .svg/.icns).
echo "Assembling Gopro360Merge.app..."
rm -rf "$STAGING"
mkdir -p "${APP_MACOS}" "${APP_RES}" "${APP}/Contents/Resources"

cp -a "${PYI_OUT}/." "${APP_RES}/"

mkdir -p "${APP_RES}/tools"
cp "$FFMPEG_BIN" "${APP_RES}/tools/ffmpeg"
cp "$FFPROBE_BIN" "${APP_RES}/tools/ffprobe"
cp "$MP4_CACHED" "${APP_RES}/tools/mp4_merge"
chmod +x "${APP_RES}/tools/ffmpeg" "${APP_RES}/tools/ffprobe" "${APP_RES}/tools/mp4_merge"
chmod +x "${APP_RES}/gopro-360-gui" "${APP_RES}/gopro-360-merge"

ICNS_SRC="${ROOT}/src/gopro_360_merge/assets/AppIcon.icns"
if [[ ! -f "$ICNS_SRC" ]]; then
  ICNS_SRC="${ROOT}/Gopro360Merge.app/Contents/Resources/AppIcon.icns"
fi
if [[ -f "$ICNS_SRC" ]]; then
  cp "$ICNS_SRC" "${APP}/Contents/Resources/AppIcon.icns"
fi

sed "s/__VERSION__/${VERSION}/g" \
  "${ROOT}/packaging/macos/Info.plist.in" > "${APP}/Contents/Info.plist"

cat > "${APP_MACOS}/Gopro360Merge" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
exec "$DIR/gopro-360-gui" "$@"
EOF
chmod +x "${APP_MACOS}/Gopro360Merge"

# CLI wrapper at zip root
cat > "${STAGING}/gopro-360-merge" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/Gopro360Merge.app/Contents/Resources/app/gopro-360-merge" "$@"
EOF
chmod +x "${STAGING}/gopro-360-merge"

cp "${ROOT}/LICENSE" "${STAGING}/LICENSE"
cp "${ROOT}/src/gopro_360_merge/vendor/NOTICE" "${STAGING}/NOTICE"
cp "${ROOT}/packaging/RELEASE_README.txt" "${STAGING}/README.txt"
cp "${ROOT}/packaging/Open GUI.command" "${STAGING}/Open GUI.command"
chmod +x "${STAGING}/Open GUI.command"

# --- Codesign (inside-out) ---
is_macho() {
  local path="$1"
  [[ -f "$path" && ! -L "$path" ]] || return 1
  file -b "$path" 2>/dev/null | grep -q 'Mach-O'
}

sign_one() {
  local path="$1"
  if [[ -n "${CODESIGN_IDENTITY}" ]]; then
    codesign --force --options runtime --timestamp \
      --entitlements "$ENTITLEMENTS" \
      --sign "$CODESIGN_IDENTITY" \
      "$path"
  else
    if [[ -d "$path" ]]; then
      codesign --force --deep --sign - "$path" 2>/dev/null \
        || codesign --force --sign - "$path"
    else
      codesign --force --sign - "$path"
    fi
  fi
}

echo "Codesigning..."
xattr -cr "$APP" 2>/dev/null || true

if [[ -n "${CODESIGN_IDENTITY}" ]]; then
  while IFS= read -r -d '' f; do
    is_macho "$f" || continue
    codesign --remove-signature "$f" 2>/dev/null || true
  done < <(find "$APP_RES" -type f -print0 2>/dev/null)
fi

while IFS= read -r -d '' f; do
  is_macho "$f" || continue
  sign_one "$f"
done < <(find "$APP_RES" -type f -print0 2>/dev/null)

if [[ -d "${APP_RES}/_internal/Python.framework" ]]; then
  sign_one "${APP_RES}/_internal/Python.framework"
fi

# Nested Mach-O inside vendor/udtacopy.zip must also be Developer ID signed
# or Apple notary rejects the archive.
UDTA_ZIP="$(find "${APP_RES}/_internal" -path '*/vendor/udtacopy.zip' -type f | head -n 1 || true)"
if [[ -n "$UDTA_ZIP" && -f "$UDTA_ZIP" ]]; then
  echo "Signing nested udtacopy in vendor zip..."
  UDTA_TMP="$(mktemp -d)"
  unzip -q "$UDTA_ZIP" -d "$UDTA_TMP"
  if [[ -f "${UDTA_TMP}/mac/udtacopy" ]]; then
    chmod +x "${UDTA_TMP}/mac/udtacopy"
    sign_one "${UDTA_TMP}/mac/udtacopy"
  fi
  UDTA_ABS="$(cd "$(dirname "$UDTA_ZIP")" && pwd)/$(basename "$UDTA_ZIP")"
  rm -f "$UDTA_ABS"
  (
    cd "$UDTA_TMP"
    zip -r -q "$UDTA_ABS" .
  )
  rm -rf "$UDTA_TMP"
fi

sign_one "${APP_MACOS}/Gopro360Merge"
sign_one "$APP"

codesign --verify --deep --strict "$APP"
echo "  codesign verify: OK"

# --- Zip (preserve symlinks) ---
make_zip() {
  rm -f "$ZIP"
  (
    cd "$DIST"
    zip -r -y -q "$(basename "$ZIP")" "$(basename "$STAGING")"
  )
}

make_zip

# --- Notarize + staple ---
if [[ "$CAN_NOTARIZE" -eq 1 ]]; then
  echo "Submitting to Apple notary service (this can take several minutes)..."
  xcrun notarytool submit "$ZIP" \
    --key "$APP_STORE_CONNECT_KEY_PATH" \
    --key-id "$APP_STORE_CONNECT_KEY_ID" \
    --issuer "$APP_STORE_CONNECT_ISSUER_ID" \
    --wait

  echo "Stapling notarization ticket..."
  xcrun stapler staple "$APP"
  xcrun stapler validate "$APP"

  # Rebuild zip with stapled .app
  make_zip
  echo "  notarize + staple: OK"
fi

echo
echo "Done."
echo "  Folder: $STAGING"
echo "  Zip:    $ZIP"
echo
echo "Publish:"
echo "  gh release upload v${VERSION} \"$ZIP\" --clobber"
