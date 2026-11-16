# Build the Windows apps (PowerShell) into build\ctt-agent-window:
#   ctt-agent.exe         console: menu + sub-commands (also usable over SSH)
#   ctt-agent-UI.exe   desktop window
#
#   .\window-app\build.ps1                             # both apps
#   .\window-app\build.ps1 -Only console               # ctt-agent.exe only
#   .\window-app\build.ps1 -Only gui                   # ctt-agent-UI.exe only
#   .\window-app\build.ps1 -Agent path\to\ctt-agent    # embed the aarch64 board agent (Deploy agent)
#   .\window-app\build.ps1 -SkipInstall                # use the current Python as is (CI)
#   .\window-app\build.ps1 -NoPythonInstall            # fail instead of installing Python
#   .\window-app\build.ps1 -OutDir some\dir            # other output folder
#
# Installs everything it needs (without -SkipInstall):
#   1. Python 3.12 for Windows, if no Python 3.10+ is found: winget, per-user (no admin).
#   2. PyInstaller + window-app\requirements.txt into its own venv build\.work\venv-win, so the
#      system Python stays clean. Later runs reuse the venv.
# From WSL, scripts/build.sh window calls this script (and embeds build/ctt-agent-aarch/ctt-agent).
# GitHub Actions builds everything: .github/workflows/btapp.yml.
param(
    [ValidateSet("all", "gui", "console")][string]$Only = "all",
    [string]$Agent = "",
    [switch]$SkipInstall,
    [switch]$NoPythonInstall,
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)   # repository root

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
    $venv = "build\.work\venv-win"
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        Write-Host "creating $venv ..."
        & $base[0] $base[1..9] -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    }
    $py = @("$venv\Scripts\python.exe")
    Write-Host "== pip: pyinstaller + window-app\requirements.txt -> $venv"
    & $py[0] -m pip install --quiet --upgrade pip pyinstaller -r window-app\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
}

if ($OutDir -eq "") { $OutDir = "build\ctt-agent-window" }
New-Item -ItemType Directory -Force -Path $OutDir, "build\.work\pyinstaller" | Out-Null
$out = (Resolve-Path $OutDir).Path
$work = (Resolve-Path "build\.work\pyinstaller").Path
$root = (Get-Location).Path
$common = @("--noconfirm", "--onefile", "--paths", "$root\window-app\src",
            "--distpath", $out, "--workpath", $work, "--specpath", $work,
            "--collect-all", "winrt", "--collect-submodules", "ctt", "--collect-all", "bleak")
if ($Agent -ne "") {
    New-Item -ItemType Directory -Force -Path "$work\agent" | Out-Null
    $src = (Resolve-Path $Agent).Path
    $dest = "$work\agent\ctt-agent"
    if ($src -ne $dest) { Copy-Item $src $dest -Force }
    $common += @("--add-data", "$dest;agent")
} else {
    Write-Warning "no -Agent: the exe cannot deploy ctt-agent to the board (Deploy agent / auto deploy)"
}

$apps = @()
if ($Only -in @("all", "gui")) {
    $apps += ,@("ctt-agent-UI", "--windowed", "$root\window-app\packaging\ctt_agent_ui_main.py")
}
if ($Only -in @("all", "console")) {
    $apps += ,@("ctt-agent", "--console", "$root\window-app\packaging\ctt_agent_main.py")
}
foreach ($app in $apps) {
    $name, $mode, $entry = $app
    Write-Host "== PyInstaller: $name =="
    & $py[0] -m PyInstaller @common $mode --name $name $entry
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($name)" }
    Write-Host "built $OutDir\$name.exe"
}
if ($apps.Count -and ($Only -in @("all", "console"))) {
    & "$out\ctt-agent.exe" --version
    if ($LASTEXITCODE -ne 0) { throw "$OutDir\ctt-agent.exe --version failed" }
}
