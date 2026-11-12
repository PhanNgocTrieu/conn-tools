# Build the Windows apps (PowerShell, from the repository root):
#   dist\ctt-bt.exe              desktop window
#   dist\ctt-agent-console.exe   console only (menu + sub-commands; usable over SSH)
#
#   .\scripts\build-btapp.ps1                             # both apps
#   .\scripts\build-btapp.ps1 -Only console               # console app only (= build-console.ps1)
#   .\scripts\build-btapp.ps1 -Only gui                   # window app only
#   .\scripts\build-btapp.ps1 -Agent path\to\ctt-agent    # also embed the aarch64 board agent
#   .\scripts\build-btapp.ps1 -SkipInstall                # use the current Python as is (CI)
#   .\scripts\build-btapp.ps1 -NoPythonInstall            # fail instead of installing Python
#
# Installs everything it needs (without -SkipInstall):
#   1. Python 3.12 for Windows, if no Python 3.10+ is found: winget, per-user (no admin).
#   2. PyInstaller + requirements-btapp.txt (winrt) into its own venv .venv-win, so the
#      system Python stays clean. Later runs reuse the venv. From WSL use scripts/build-windows.sh, which calls this script.
# The aarch64 agent is built in WSL: scripts/build-dut.sh --generic --static --strip
# (build/dut/agent/ctt-agent). GitHub Actions builds everything: .github/workflows/btapp.yml.
param(
    [ValidateSet("all", "gui", "console")][string]$Only = "all",
    [string]$Agent = "",
    [switch]$SkipInstall,
    [switch]$NoPythonInstall
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Test-Python([string[]]$cmd) {
    # Returns $true for a working Python 3.10+. The "python" in WindowsApps is only a
    # Microsoft Store shortcut that prints an error, so the command is really run.
    try {
        $ErrorActionPreference = "Continue"
        $v = & $cmd[0] $cmd[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        return ($LASTEXITCODE -eq 0 -and $v -and [version]"$v" -ge [version]"3.10")
    } catch { return $false }
}

function Find-Python {
    foreach ($cand in @("py -3", "python", "python3")) {
        $parts = $cand.Split(" ")
        if ((Get-Command $parts[0] -ErrorAction SilentlyContinue) -and (Test-Python $parts)) {
            return ,$parts
        }
    }
    # Fresh installs are not on PATH until a new terminal: look in the default locations.
    $dirs = @("$env:LOCALAPPDATA\Programs\Python", "$env:ProgramFiles")
    $found = Get-ChildItem -Path $dirs -Filter "Python3*" -Directory -ErrorAction SilentlyContinue |
             Sort-Object Name -Descending
    foreach ($d in $found) {
        $exe = Join-Path $d.FullName "python.exe"
        if ((Test-Path $exe) -and (Test-Python @($exe))) { return ,@($exe) }
    }
    return $null
}

function Install-Python {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw ("Python 3.10+ not found and winget is not available.`n" +
               "Install Python 3.12 from https://www.python.org/downloads/windows/ and run again.")
    }
    Write-Host "== Python 3.10+ not found: installing Python 3.12 with winget (per-user) ..."
    & winget install --exact --id Python.Python.3.12 --scope user --silent `
        --accept-package-agreements --accept-source-agreements
    # winget returns non-zero also for "already installed"; the check below decides.
}

if ($SkipInstall) {
    $py = @("python")
} else {
    $base = Find-Python
    if (-not $base) {
        if ($NoPythonInstall) {
            throw ("Python 3.10+ for Windows not found (the WindowsApps 'python' is only a Store " +
                   "shortcut). Install it: winget install Python.Python.3.12")
        }
        Install-Python
        $base = Find-Python
        if (-not $base) { throw "Python install did not produce a usable python.exe" }
    }
    Write-Host "Python: $($base -join ' ')"
    $venv = ".venv-win"
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        Write-Host "creating $venv ..."
        & $base[0] $base[1..9] -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    }
    $py = @("$venv\Scripts\python.exe")
    Write-Host "== pip: pyinstaller + requirements-btapp.txt -> $venv"
    & $py[0] -m pip install --quiet --upgrade pip pyinstaller -r requirements-btapp.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

$common = @("--noconfirm", "--onefile", "--paths", ".", "--collect-all", "winrt",
            "--collect-submodules", "ctt")
if ($Agent -ne "") {
    New-Item -ItemType Directory -Force -Path dist\agent | Out-Null
    $src = (Resolve-Path $Agent).Path
    $dest = Join-Path (Get-Location) "dist\agent\ctt-agent"
    if ($src -ne $dest) { Copy-Item $src $dest -Force }
    $common += @("--add-data", "dist\agent\ctt-agent;agent")
} else {
    Write-Warning "no -Agent: the exe cannot deploy ctt-agent to the board (Deploy agent / auto deploy)"
}

$apps = @()
if ($Only -in @("all", "gui")) {
    $apps += ,@("ctt-bt", "--windowed", "packaging\ctt_bt_main.py")
}
if ($Only -in @("all", "console")) {
    $apps += ,@("ctt-agent-console", "--console", "packaging\ctt_agent_console_main.py")
}
foreach ($app in $apps) {
    $name, $mode, $entry = $app
    Write-Host "== PyInstaller: $name =="
    & $py[0] -m PyInstaller @common $mode --name $name $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($name)" }
    Write-Host "built dist\$name.exe"
}
if ($apps.Count -and ($Only -in @("all", "console"))) {
    & "dist\ctt-agent-console.exe" --version
    if ($LASTEXITCODE -ne 0) { throw "dist\ctt-agent-console.exe --version failed" }
}
