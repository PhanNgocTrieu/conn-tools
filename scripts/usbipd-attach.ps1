<#
.SYNOPSIS
  Attach a USB device (BT dongle, WiFi dongle, DUT USB port) to WSL with usbipd-win.

.EXAMPLE
  # list devices
  .\usbipd-attach.ps1
  # attach by bus id (first time needs an elevated PowerShell for 'bind')
  .\usbipd-attach.ps1 -BusId 2-3
  # attach by VID:PID, re-attach automatically after unplug/replug
  .\usbipd-attach.ps1 -HardwareId 0bda:8771 -AutoAttach

.NOTES
  Install usbipd-win once:  winget install usbipd
  A device attached to WSL is no longer usable by Windows until detached:
  usbipd detach --busid <id>
#>
param(
    [string]$BusId,
    [string]$HardwareId,
    [switch]$AutoAttach
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command usbipd -ErrorAction SilentlyContinue)) {
    Write-Error "usbipd-win is not installed. Run: winget install usbipd"
}

if (-not $BusId -and -not $HardwareId) {
    usbipd list
    Write-Host "`nUse -BusId <id> or -HardwareId <vid:pid> to attach a device to WSL."
    exit 0
}

$selector = if ($BusId) { @("--busid", $BusId) } else { @("--hardware-id", $HardwareId) }

# 'bind' shares the device; it needs admin rights once per device.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
try {
    usbipd bind @selector
} catch {
    if (-not $isAdmin) {
        Write-Warning "bind failed - if the device is not shared yet, rerun once in an elevated PowerShell."
    }
}

$attach = @("attach", "--wsl") + $selector
if ($AutoAttach) { $attach += "--auto-attach" }
usbipd @attach
Write-Host "Attached. In WSL check with: lsusb  /  hciconfig -a  /  iw dev"
