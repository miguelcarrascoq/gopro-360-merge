# Code signing policy

Free code signing provided by [SignPath.io](https://signpath.io), certificate by [SignPath Foundation](https://signpath.org/).

## Team roles

This repository is maintained by a single individual maintainer:

| Role | Who |
|------|-----|
| Authors / Committers / Reviewers | [@miguelcarrascoq](https://github.com/miguelcarrascoq) |
| Approvers (SignPath signing requests) | [@miguelcarrascoq](https://github.com/miguelcarrascoq) |

Pull requests from others are reviewed before merge. Every SignPath signing request for a release will be manually approved by the maintainer.

## What is signed

Windows release artifacts built by GitHub Actions from this repository (`gopro-360-gui.exe`, `gopro-360-merge.exe`, and bundled `tools\mp4_merge.exe` when included in the signing configuration). Third-party tools such as ffmpeg/ffprobe may be redistributed unsigned or with their original vendor signatures and are not re-signed with the SignPath Foundation certificate.

## Privacy

See [privacy.md](privacy.md).
