GoPro 360 Merge — release package

Run:
  gopro-360-gui       Desktop GUI  (Windows: gopro-360-gui.exe)
  gopro-360-merge     Interactive CLI  (Windows: gopro-360-merge.exe)

This archive is self-contained: ffmpeg, ffprobe, and mp4-merge live in the
tools/ folder next to the executables. Keep that folder beside the app files
(and keep _internal/ next to them on macOS). Do not move the binaries out of
this folder alone.

Unzip anywhere and run. Python is not required.

macOS — REQUIRED after download from the internet
-------------------------------------------------
Chrome/Safari mark the unzipped folder as quarantined. Double-clicking
gopro-360-gui then fails with "Python.framework Not Opened" until you clear
that flag.

Easiest: double-click  Open GUI.command  (allow it in Terminal / Privacy if
macOS asks). That clears quarantine and starts the GUI.

Or run once in Terminal (use your real folder path):

  xattr -cr "/path/to/gopro-360-merge-*-macos-*"
  open "/path/to/gopro-360-merge-*-macos-*/gopro-360-gui"

These builds are ad-hoc signed, not Apple-notarized.
