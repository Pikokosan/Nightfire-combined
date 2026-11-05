param([switch]$RestartOnly)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DriverPath = Join-Path $RepoRoot "tools/android-sdk/emulator/drivers"
$LogPath = Join-Path $RepoRoot $(if ($RestartOnly) { "dist/android/usb-driver-restart.log" } else { "dist/android/usb-driver-install.log" })
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$Principal = New-Object Security.Principal.WindowsPrincipal($Identity)
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script as Windows administrator to install the USB passthrough drivers."
}
Start-Transcript -Path $LogPath -Force
Push-Location $DriverPath
try {
    if ($RestartOnly) {
        Restart-Service -Name UsbAssist -Force
    } else {
        & .\Install_Drivers.bat
    }
    & sc.exe query UsbAssist
    if ($LASTEXITCODE -ne 0) {
        throw "UsbAssist service is not installed. See $LogPath."
    }
    if ((Get-Service -Name UsbAssist).Status -ne "Running") {
        throw "UsbAssist service is not running. See $LogPath."
    }
}
finally {
    Pop-Location
    Stop-Transcript
}
