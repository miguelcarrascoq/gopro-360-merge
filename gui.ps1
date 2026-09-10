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
    Write-Host "> $Message"
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

# Must match APP_USER_MODEL_ID in gopro_360_merge.gui
$AppUserModelId = "com.gopro360merge.gui"

function Set-ShortcutAppUserModelId {
    param(
        [Parameter(Mandatory = $true)][string]$LnkPath,
        [Parameter(Mandatory = $true)][string]$AppId
    )
    if (-not ("Gopro360Merge.ShortcutAumid" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

namespace Gopro360Merge {
    public static class ShortcutAumid {
        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        private class CShellLink { }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
        private interface IShellLinkW {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile, int cchMaxPath, IntPtr pfd, int fFlags);
            void GetIDList(out IntPtr ppidl);
            void SetIDList(IntPtr pidl);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName, int cchMaxName);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir, int cchMaxPath);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs, int cchMaxArgs);
            void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
            void GetHotkey(out short pwHotkey);
            void SetHotkey(short wHotkey);
            void GetShowCmd(out int piShowCmd);
            void SetShowCmd(int iShowCmd);
            void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath, int cchIconPath, out int piIcon);
            void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
            void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
            void Resolve(IntPtr hwnd, int fFlags);
            void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
        }

        [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
        private interface IPropertyStore {
            uint GetCount(out uint cProps);
            uint GetAt(uint iProp, out PropertyKey pkey);
            uint GetValue(ref PropertyKey key, [Out] PropVariant pv);
            uint SetValue(ref PropertyKey key, PropVariant pv);
            uint Commit();
        }

        [StructLayout(LayoutKind.Sequential, Pack = 4)]
        private struct PropertyKey {
            public Guid fmtid;
            public int pid;
            public PropertyKey(Guid fmtid, int pid) { this.fmtid = fmtid; this.pid = pid; }
        }

        [StructLayout(LayoutKind.Explicit)]
        private sealed class PropVariant : IDisposable {
            [FieldOffset(0)] ushort vt;
            [FieldOffset(8)] IntPtr ptr;
            public PropVariant(string value) {
                vt = 31; // VT_LPWSTR
                ptr = Marshal.StringToCoTaskMemUni(value);
            }
            public void Dispose() {
                PropVariantClear(this);
                GC.SuppressFinalize(this);
            }
            ~PropVariant() { Dispose(); }
        }

        [DllImport("ole32.dll")]
        private static extern int PropVariantClear([In, Out] PropVariant pvar);

        public static void Set(string lnkPath, string appId) {
            var link = (IShellLinkW)new CShellLink();
            var file = (IPersistFile)link;
            file.Load(lnkPath, 2); // STGM_READWRITE
            var store = (IPropertyStore)link;
            var key = new PropertyKey(new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), 5);
            using (var pv = new PropVariant(appId)) {
                if (store.SetValue(ref key, pv) > 1) throw new InvalidOperationException("SetValue failed");
                if (store.Commit() > 1) throw new InvalidOperationException("Commit failed");
            }
            file.Save(lnkPath, true);
            Marshal.FinalReleaseComObject(link);
        }
    }
}
"@
    }
    [Gopro360Merge.ShortcutAumid]::Set($LnkPath, $AppId)
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
        Set-ShortcutAppUserModelId -LnkPath $lnk -AppId $AppUserModelId
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
