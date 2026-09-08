# Bootstrap deps if needed, then open the gopro-360-merge desktop GUI.
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

$Venv = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvGui = Join-Path $Venv "Scripts\gopro-360-gui.exe"

function Die([string]$Message) {
    Write-Error "error: $Message"
    exit 1
}

function Info([string]$Message) {
    Write-Host "→ $Message"
}

function Get-SystemPython {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $ver = & py -3 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $ver) {
                return @{ Exe = "py"; Args = @("-3") }
            }
        } catch { }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @{ Exe = "python"; Args = @() }
    }
    Die "Python 3.11+ is required. Install Python and retry."
}

function Require-Python {
    $py = Get-SystemPython
    $verArgs = $py.Args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
    $ver = & $py.Exe @verArgs
    if ($LASTEXITCODE -ne 0) {
        Die "Python 3.11+ is required. Install Python and retry."
    }
    $parts = $ver.Trim().Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 11)) {
        Die "Python 3.11+ is required (found $ver)."
    }
    return $py
}

function Ensure-Venv($SystemPython) {
    if (-not (Test-Path $VenvPython)) {
        Info "Creating virtualenv at .venv"
        $venvArgs = $SystemPython.Args + @("-m", "venv", $Venv)
        & $SystemPython.Exe @venvArgs
        if ($LASTEXITCODE -ne 0) {
            Die "Failed to create virtualenv."
        }
    }
}

function Ensure-Package {
    $needInstall = -not (Test-Path $VenvGui)
    if (-not $needInstall) {
        & $VenvPython -c "import gopro_360_merge, customtkinter" 2>$null
        if ($LASTEXITCODE -ne 0) {
            $needInstall = $true
        }
    }
    if ($needInstall) {
        Info "Installing gopro-360-merge into .venv"
        & $VenvPython -m pip install -q -e .
        if ($LASTEXITCODE -ne 0) {
            Die "pip install failed."
        }
    }
}

function Test-OnPath([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
        [System.Environment]::GetEnvironmentVariable("Path", "User")
}

function Get-MissingFfmpegTools {
    $missing = @()
    if (-not (Test-OnPath "ffmpeg")) { $missing += "ffmpeg" }
    if (-not (Test-OnPath "ffprobe")) { $missing += "ffprobe" }
    return $missing
}

function Ensure-Ffmpeg {
    Refresh-Path
    $missing = Get-MissingFfmpegTools
    if ($missing.Count -eq 0) {
        return
    }

    if (Test-OnPath "winget") {
        Info "Installing ffmpeg via winget (missing: $($missing -join ', '))"
        & winget install --id Gyan.FFmpeg -e --accept-package-agreements --accept-source-agreements
        $wingetExit = $LASTEXITCODE
        Refresh-Path
        $missing = Get-MissingFfmpegTools
        if ($missing.Count -eq 0) {
            return
        }
        if ($wingetExit -ne 0) {
            Die "winget install of ffmpeg failed. Install manually from https://ffmpeg.org"
        }
        Die "ffmpeg installed but not on PATH yet. Open a new terminal and retry."
    }

    Die "Missing required tools: $($missing -join ', '). Install ffmpeg (includes ffprobe), e.g. winget install Gyan.FFmpeg"
}

function Ensure-Shortcut {
    $ico = Join-Path $ScriptDir "src\gopro_360_merge\assets\app_icon.ico"
    $bat = Join-Path $ScriptDir "gui.bat"
    $lnk = Join-Path $ScriptDir "Gopro360Merge.lnk"
    if (-not (Test-Path -LiteralPath $ico)) {
        return
    }
    if (-not (Test-Path -LiteralPath $bat)) {
        return
    }
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($lnk)
        $shortcut.TargetPath = $bat
        $shortcut.WorkingDirectory = $ScriptDir
        $shortcut.IconLocation = "$ico,0"
        $shortcut.Description = "GoPro 360 Merge"
        $shortcut.Save()
        Info "Shortcut updated: Gopro360Merge.lnk"
    } catch {
        # Shortcut is optional; continue launching the GUI.
    }
}

$SystemPython = Require-Python
Ensure-Venv $SystemPython
Ensure-Package
Ensure-Ffmpeg
Ensure-Shortcut

Write-Host ""
Info "Opening GUI…"
& $VenvGui
exit $LASTEXITCODE
