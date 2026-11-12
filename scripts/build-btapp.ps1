# Build the Windows apps (PowerShell, from the repository root):
#   dist\ctt-bt.exe              desktop window
#   dist\ctt-agent-console.exe   console only (menu + sub-commands; usable over SSH)
#
#   .\scripts\build-btapp.ps1                             # both apps
#   .\scripts\build-btapp.ps1 -Only console               # console app only (= build-console.ps1)
#   .\scripts\build-btapp.ps1 -Only gui                   # window app only
#   .\scripts\build-btapp.ps1 -Agent path\to\ctt-agent    # also embed the aarch64 board agent
#   .\scripts\build-btapp.ps1 -SkipInstall                # use the current Python as is (CI)
#
# Needs Python 3.10+ for Windows (python.org or `winget install Python.Python.3.12`).
# Without -SkipInstall the build runs in its own venv (.venv-win) so the system Python
# stays clean. From WSL use scripts/build-windows.sh, which calls this script.
# The aarch64 agent is built in WSL: scripts/build-dut.sh --generic --static --strip
# (build/dut/agent/ctt-agent). GitHub Actions builds everything: .github/workflows/btapp.yml.
param(
    [ValidateSet("all", "gui", "console")][string]$Only = "all",
    [string]$Agent = "",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Find-Python {
    # The "python" from WindowsApps is only a Microsoft Store shortcut: run it to be sure.
    foreach ($cand in @("py -3", "python", "python3")) {
        $parts = $cand.Split(" ")
        if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
        try {
            $ErrorActionPreference = "Continue"
            $v = & $parts[0] $parts[1..9] -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v -and [version]"$v" -ge [version]"3.10") { return ,$parts }
        } catch { }
    }
    throw ("Python 3.10+ for Windows not found (the WindowsApps 'python' is only a Store shortcut).`n" +
           "Install it: winget install Python.Python.3.12   then open a new terminal.")
}

if ($SkipInstall) {
    $py = @("python")
} else {
    $base = Find-Python
    $venv = ".venv-win"
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        Write-Host "creating $venv ..."
        & $base[0] $base[1..9] -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
    }
    $py = @("$venv\Scripts\python.exe")
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
