# Changelog

## 1.0.1

- GUI and bootstrap scripts use English labels/messages for a consistent cross-platform UX.
- Windows release rebuild with the latest packaging and GUI changes (`gopro-360-merge-1.0.1-windows-x64.zip`).
- macOS Apple Silicon release as `Gopro360Merge.app`, signed with Developer ID (Servicios Globales Tecnologicos Limitada) and notarized (`gopro-360-merge-1.0.1-macos-arm64.zip`).

## 1.0.0

- First binary release for Windows (self-contained zip with GUI, CLI, ffmpeg, ffprobe, and mp4-merge).
- macOS Apple Silicon binary (`gopro-360-merge-1.0.0-macos-arm64.zip`) with the same self-contained layout.
- macOS zip preserves `_internal/Python` symlinks and ad-hoc codesigns the onedir tree (fixes Gatekeeper load of Python.framework).
- macOS release includes `Open GUI.command` to clear download quarantine before first launch.
- Prefer `tools/` next to the frozen executable when resolving external binaries.
- Single-sourced package version via `gopro_360_merge.__version__`.
