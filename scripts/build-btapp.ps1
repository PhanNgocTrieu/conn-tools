# Build ctt-bt.exe on Windows (PowerShell, from the repository root).
#
#   .\scripts\build-btapp.ps1                       # app only
#   .\scripts\build-btapp.ps1 -Agent path\to\ctt-agent   # also embed the aarch64 agent
#
# Needs Python 3.10+ for Windows (python.org or winget install Python.Python.3.12).
# The aarch64 agent is built in WSL: scripts/build-dut.sh --generic --static --strip
# (build/dut/agent/ctt-agent). GitHub Actions builds both: .github/workflows/btapp.yml.
param([string]$Agent = "")
$ErrorActionPreference = "Stop"
python -m pip install --upgrade pip pyinstaller -r requirements-btapp.txt
$addData = @()
if ($Agent -ne "") {
    New-Item -ItemType Directory -Force -Path dist\agent | Out-Null
    Copy-Item $Agent dist\agent\ctt-agent
    $addData = @("--add-data", "dist\agent\ctt-agent;agent")
}
python -m PyInstaller --noconfirm --onefile --windowed --name ctt-bt `
    --paths . --collect-all winrt `
    --hidden-import ctt.btapp.gui --hidden-import ctt.fw --hidden-import ctt.bt `
    @addData packaging\ctt_bt_main.py
Write-Host "built dist\ctt-bt.exe"
