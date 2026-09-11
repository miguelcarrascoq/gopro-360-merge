GoPro 360 Merge — release package

macOS
-----
Open Gopro360Merge.app (double-click).

CLI from Terminal (same folder):
  ./gopro-360-merge

Keep the unzipped folder intact (the .app embeds _internal/ and tools/).

If Gatekeeper still blocks an unsigned/ad-hoc build after download:
  xattr -cr /path/to/this/folder
  open Gopro360Merge.app
Or double-click Open GUI.command.

Notarized Developer ID builds should open without xattr.

Windows
-------
Run:
  gopro-360-gui.exe       Desktop GUI
  gopro-360-merge.exe     Interactive CLI

Keep tools\ next to the executables. Python is not required.

Official releases may be Authenticode-signed via SignPath Foundation
(publisher may show as "SignPath Foundation"). If SmartScreen warns on
first launch: More info → Run anyway.
