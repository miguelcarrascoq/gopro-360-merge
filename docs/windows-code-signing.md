# Windows code signing (free via SignPath Foundation)

GitHub Releases for Windows can be Authenticode-signed **at no cost** through
[SignPath Foundation](https://signpath.org/) (open-source program). The publisher
shown in the EXE properties will be **SignPath Foundation**, not a personal or
company name.

Self-signed certificates do **not** clear SmartScreen for downloads from the internet.

## What you get

- Free OV-level Authenticode on `gopro-360-gui.exe`, `gopro-360-merge.exe`, and `tools\mp4_merge.exe`
- Signing runs in GitHub Actions (GitHub-hosted runners only — required by SignPath OSS)
- Workflow: [`.github/workflows/windows-signed-release.yml`](../.github/workflows/windows-signed-release.yml)

SmartScreen may still warn briefly on brand-new certificates until reputation builds.
That is still far better than an unsigned download.

## One-time setup (maintainer)

### 1. Apply to SignPath Foundation

1. Open https://signpath.org/ and use **Apply for Free Code Signing**.
2. Provide:
   - Repository: `https://github.com/miguelcarrascoq/gopro-360-merge`
   - License: MIT
   - Releases: `https://github.com/miguelcarrascoq/gopro-360-merge/releases`
   - Short description of the app
3. Wait for approval (can take days). They may ask for MFA and manual approval of signing requests.

### 2. Configure SignPath.io project

After approval:

1. Create / open the organization and project (suggested slug: `gopro-360-merge`).
2. Install the **SignPath GitHub App** and grant access to this repository.
3. Link Trusted Build System **GitHub.com** to the project.
4. Add an Artifact Configuration:
   - Slug: `windows-portable`
   - XML: copy from [`.github/signpath/windows-portable.xml`](../.github/signpath/windows-portable.xml)
5. Note the **signing policy** slug (often `test-signing` first, then `release-signing`).
6. Create an API token with submitter permission.

### 3. GitHub repository secrets / variables

| Name | Type | Example |
|------|------|---------|
| `SIGNPATH_API_TOKEN` | Secret | (from SignPath) |
| `SIGNPATH_ORGANIZATION_ID` | Secret or Variable | UUID from SignPath |
| `SIGNPATH_PROJECT_SLUG` | Variable | `gopro-360-merge` |
| `SIGNPATH_SIGNING_POLICY_SLUG` | Variable | `test-signing` or `release-signing` |
| `SIGNPATH_ARTIFACT_CONFIGURATION_SLUG` | Variable | `windows-portable` |

### 4. Run a signed build

- **Actions → Windows signed release → Run workflow**, or
- Push a version tag (`v1.0.2`, …)

If secrets are missing, the workflow still builds an **unsigned** zip artifact and prints setup hints.
With secrets configured, it submits to SignPath, waits for completion (approve in the SignPath UI if the policy requires it), then uploads the signed zip and can attach it to the GitHub Release for that tag.

## Local unsigned builds

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1
```

Local builds stay unsigned. Official Windows Releases should use the GitHub Actions + SignPath path.
