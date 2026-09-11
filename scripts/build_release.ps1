# Build a self-contained Windows release zip for GitHub Releases.
# Usage (from repo root):  powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Get-PackageVersion {
    $init = Join-Path $Root "src\gopro_360_merge\__init__.py"
    $text = Get-Content -Raw -Path $init
    if ($text -match '__version__\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not read __version__ from $init"
}

$Version = Get-PackageVersion
$Platform = "windows-x64"
$ArtifactName = "gopro-360-merge-$Version-$Platform"
$CacheDir = Join-Path $Root ".release-cache"
$VenvDir = Join-Path $Root ".venv-release"
$DistDir = Join-Path $Root "dist"
$BuildDir = Join-Path $Root "build"
$StagingDir = Join-Path $DistDir $ArtifactName
$ZipPath = Join-Path $DistDir "$ArtifactName.zip"
$PyInstallerOut = Join-Path $DistDir "gopro360merge"

$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$Mp4MergeUrl = "https://github.com/gyroflow/mp4-merge/releases/download/v0.1.11/mp4_merge-windows64.exe"

Write-Host "Building $ArtifactName"

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null

# --- Python venv + deps ---
# Prefer `python` first (GitHub Actions setup-python), then the Windows py launcher.
$Py = $null
foreach ($candidate in @(
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "py"; Args = @("-3.12") },
        @{ Cmd = "py"; Args = @("-3.11") },
        @{ Cmd = "py"; Args = @("-3") }
    )) {
    try {
        $verArgs = $candidate.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
        $ver = & $candidate.Cmd @verArgs 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match '^(3\.(1[1-9]|[2-9]\d))$') {
            $Py = $candidate
            break
        }
    } catch { }
}
if (-not $Py) {
    throw "Python 3.11+ is required to build the release."
}

if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    Write-Host "Creating build venv at $VenvDir"
    & $Py.Cmd @($Py.Args + @("-m", "venv", $VenvDir))
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip wheel
& $VenvPython -m pip install -e "$Root" pyinstaller

# --- ffmpeg essentials ---
$FfmpegZip = Join-Path $CacheDir "ffmpeg-release-essentials.zip"
if (-not (Test-Path $FfmpegZip)) {
    Write-Host "Downloading ffmpeg essentials..."
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $FfmpegZip
}

$FfmpegExtract = Join-Path $CacheDir "ffmpeg-essentials"
if (-not (Test-Path (Join-Path $FfmpegExtract ".ok"))) {
    if (Test-Path $FfmpegExtract) {
        Remove-Item -Recurse -Force $FfmpegExtract
    }
    New-Item -ItemType Directory -Force -Path $FfmpegExtract | Out-Null
    Expand-Archive -Path $FfmpegZip -DestinationPath $FfmpegExtract -Force
    New-Item -ItemType File -Path (Join-Path $FfmpegExtract ".ok") | Out-Null
}

$FfmpegBin = Get-ChildItem -Path $FfmpegExtract -Recurse -Filter "ffmpeg.exe" |
    Where-Object { $_.DirectoryName -match '\\bin$' } |
    Select-Object -First 1
if (-not $FfmpegBin) {
    throw "ffmpeg.exe not found inside essentials zip"
}
$FfprobeBin = Join-Path $FfmpegBin.DirectoryName "ffprobe.exe"
if (-not (Test-Path $FfprobeBin)) {
    throw "ffprobe.exe missing next to $($FfmpegBin.FullName)"
}

# --- mp4-merge ---
$Mp4MergeCached = Join-Path $CacheDir "mp4_merge.exe"
if (-not (Test-Path $Mp4MergeCached)) {
    Write-Host "Downloading mp4-merge..."
    Invoke-WebRequest -Uri $Mp4MergeUrl -OutFile $Mp4MergeCached
}

# --- PyInstaller ---
Write-Host "Running PyInstaller..."
if (Test-Path $PyInstallerOut) {
    Remove-Item -Recurse -Force $PyInstallerOut
}
& $VenvPython -m PyInstaller `
    --noconfirm `
    --clean `
    --distpath $DistDir `
    --workpath $BuildDir `
    (Join-Path $Root "packaging\gopro360merge.spec")

if (-not (Test-Path $PyInstallerOut)) {
    throw "PyInstaller output missing: $PyInstallerOut"
}

# --- Stage artifact ---
if (Test-Path $StagingDir) {
    Remove-Item -Recurse -Force $StagingDir
}
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null
Copy-Item -Path (Join-Path $PyInstallerOut "*") -Destination $StagingDir -Recurse -Force

$ToolsDir = Join-Path $StagingDir "tools"
New-Item -ItemType Directory -Force -Path $ToolsDir | Out-Null
Copy-Item -Path $FfmpegBin.FullName -Destination (Join-Path $ToolsDir "ffmpeg.exe") -Force
Copy-Item -Path $FfprobeBin -Destination (Join-Path $ToolsDir "ffprobe.exe") -Force
Copy-Item -Path $Mp4MergeCached -Destination (Join-Path $ToolsDir "mp4_merge.exe") -Force

Copy-Item -Path (Join-Path $Root "LICENSE") -Destination (Join-Path $StagingDir "LICENSE") -Force
Copy-Item -Path (Join-Path $Root "src\gopro_360_merge\vendor\NOTICE") -Destination (Join-Path $StagingDir "NOTICE") -Force
Copy-Item -Path (Join-Path $Root "packaging\RELEASE_README.txt") -Destination (Join-Path $StagingDir "README.txt") -Force

# --- Zip ---
if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}
Write-Host "Creating $ZipPath"
Compress-Archive -Path $StagingDir -DestinationPath $ZipPath -Force

Write-Host ""
Write-Host "Done."
Write-Host "  Folder: $StagingDir"
Write-Host "  Zip:    $ZipPath"
Write-Host ""
Write-Host "Publish (when ready):"
Write-Host "  git tag v$Version"
Write-Host "  git push origin v$Version"
Write-Host "  gh release create v$Version `"$ZipPath`" --title `"v$Version`" --notes-file CHANGELOG.md"
