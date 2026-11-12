# Build only dist\ctt-agent-console.exe (console app, no window).
#   .\scripts\build-console.ps1
#   .\scripts\build-console.ps1 -Agent build\dut\agent\ctt-agent   # embed the board agent
# Same options as build-btapp.ps1 (-Agent, -SkipInstall, -NoPythonInstall); installs Python
# and the pip packages itself when they are missing.
param([string]$Agent = "", [switch]$SkipInstall, [switch]$NoPythonInstall)
& (Join-Path $PSScriptRoot "build-btapp.ps1") -Only console -Agent $Agent -SkipInstall:$SkipInstall -NoPythonInstall:$NoPythonInstall
exit $LASTEXITCODE
