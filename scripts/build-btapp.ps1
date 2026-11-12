# Build the Windows apps (PowerShell, from the repository root):
#   dist\ctt-bt.exe              desktop window
#   dist\ctt-agent-console.exe   console only (menu + sub-commands; usable over SSH)
#
#   .\scripts\build-btapp.ps1                             # apps only
#   .\scripts\build-btapp.ps1 -Agent path\to\ctt-agent    # also embed the aarch64 board agent
#
# Needs Python 3.10+ for Windows (python.org or `winget install Python.Python.3.12`).
# The aarch64 agent is built in WSL: scripts/build-dut.sh --generic --static --strip
# (build/dut/agent/ctt-agent). GitHub Actions builds everything: .github/workflows/btapp.yml.
param([string]$Agent = "", [switch]$SkipInstall)
$ErrorActionPreference = "Stop"
if (-not $SkipInstall) {
    python -m pip install --upgrade pip pyinstaller -r requirements-btapp.txt
}
$common = @("--noconfirm", "--onefile", "--paths", ".", "--collect-all", "winrt",
            "--collect-submodules", "ctt")
if ($Agent -ne "") {
    New-Item -ItemType Directory -Force -Path dist\agent | Out-Null
    $src = (Resolve-Path $Agent).Path
    $dest = Join-Path (Get-Location) "dist\agent\ctt-agent"
    if ($src -ne $dest) { Copy-Item $src $dest -Force }
    $common += @("--add-data", "dist\agent\ctt-agent;agent")
}
python -m PyInstaller @common --windowed --name ctt-bt packaging\ctt_bt_main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (ctt-bt)" }
python -m PyInstaller @common --console --name ctt-agent-console packaging\ctt_agent_console_main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (ctt-agent-console)" }
Write-Host "built dist\ctt-bt.exe and dist\ctt-agent-console.exe"
