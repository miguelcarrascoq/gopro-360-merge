# Bootstrap deps if needed, confirm GoPro folder, then crop a .360 block.
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
Set-Location $ScriptDir

$Venv = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$VenvCli = Join-Path $Venv "Scripts\gopro-360-merge.exe"

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
    $needInstall = -not (Test-Path $VenvCli)
    if (-not $needInstall) {
        & $VenvPython -c "import gopro_360_merge" 2>$null
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

function Confirm-Yes([string]$Prompt) {
    $reply = Read-Host "$Prompt [Y/n]"
    if ([string]::IsNullOrWhiteSpace($reply)) {
        $reply = "Y"
    }
    return $reply -match '^(Y|y|yes|YES)$'
}

function Resolve-Directory([string]$Dir) {
    if ($Dir.StartsWith("~")) {
        $Dir = $Dir -replace "^~", $HOME
    }
    if (Test-Path -LiteralPath $Dir -PathType Container) {
        return (Resolve-Path -LiteralPath $Dir).Path
    }
    return $Dir
}

function Test-YesFlag($FlagList) {
    foreach ($f in @($FlagList)) {
        if ($f -eq "-y" -or $f -eq "--yes") {
            return $true
        }
    }
    return $false
}

# If the folder prompt includes flags (e.g. "D:\GoPro --start 00:00:27 -y"), split them.
function Split-FolderAndFlags([string]$Raw) {
    $raw = $Raw.Trim()
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @{ Dir = "."; ExtraFlags = @() }
    }
    if (Test-Path -LiteralPath $raw -PathType Container) {
        return @{ Dir = $raw; ExtraFlags = @() }
    }
    if ($raw -match '^(?<dir>.+?)\s+(?<flags>-(?:-)?[A-Za-z].*)$') {
        $dirPart = $Matches["dir"].Trim().Trim('"')
        $flagTokens = $Matches["flags"] -split '\s+' | Where-Object { $_ -ne "" }
        return @{ Dir = $dirPart; ExtraFlags = @($flagTokens) }
    }
    return @{ Dir = $raw; ExtraFlags = @() }
}

$DirArg = $null
$Flags = @()
if ($args.Count -gt 0 -and -not ($args[0] -like "-*")) {
    $DirArg = $args[0]
    if ($args.Count -gt 1) {
        $Flags = $args[1..($args.Count - 1)]
    }
} else {
    $Flags = @($args)
}

$SystemPython = Require-Python
Ensure-Venv $SystemPython
Ensure-Package
Ensure-Ffmpeg

Write-Host ""
if ($null -ne $DirArg -and $DirArg -ne "") {
    $Dir = Resolve-Directory $DirArg
} else {
    $folderInput = Read-Host "Carpeta GS*.360 (puedes pegar --start / --end / -y) [.]"
    if ([string]::IsNullOrWhiteSpace($folderInput)) {
        $folderInput = "."
    }
    $parsed = Split-FolderAndFlags $folderInput
    $Dir = Resolve-Directory $parsed.Dir
    if ($parsed.ExtraFlags.Count -gt 0) {
        $Flags = @($Flags) + @($parsed.ExtraFlags)
    }
}

Write-Host "Carpeta: $Dir"
if (-not (Test-YesFlag $Flags)) {
    if (-not (Confirm-Yes "Usar esta carpeta?")) {
        Write-Host "Cancelado."
        exit 0
    }
}

if (-not (Test-Path -LiteralPath $Dir -PathType Container)) {
    Die "Not a directory: $Dir"
}

Write-Host ""
& $VenvCli crop $Dir @Flags
exit $LASTEXITCODE
