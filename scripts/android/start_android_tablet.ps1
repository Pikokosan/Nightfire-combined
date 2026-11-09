param(
    [switch]$UsbRadio,
    [string]$RadioSerial = "339F9FB23EBB3CE7"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$env:ANDROID_HOME = Join-Path $RepoRoot "tools/android-sdk"
$env:ANDROID_AVD_HOME = Join-Path $RepoRoot "tools/android-avd"
$Emulator = Join-Path $env:ANDROID_HOME "emulator/emulator.exe"
$Arguments = @(
    "-avd", "Nightfire_Tablet", "-port", "5554",
    "-gpu", "software", "-memory", "3072", "-cores", "4",
    "-no-snapshot", "-no-boot-anim", "-no-audio",
    "-camera-back", "none", "-camera-front", "none", "-scale", "0.4"
)
if ($UsbRadio) {
    $Driver = Get-Service -Name UsbAssist -ErrorAction SilentlyContinue
    if (-not $Driver -or $Driver.Status -ne "Running") {
        throw "Install the bundled USB passthrough drivers first using install_android_usb_passthrough.ps1 as administrator."
    }
    $DeviceId = "USB\VID_239A&PID_00B3\$RadioSerial"
    $Locations = (Get-PnpDeviceProperty -InstanceId $DeviceId -KeyName DEVPKEY_Device_LocationPaths).Data
    $Location = @($Locations | Where-Object { $_ -like "PCIROOT*" }) | Select-Object -First 1
    $Ports = @([regex]::Matches($Location, '#USB\((\d+)\)') | ForEach-Object { $_.Groups[1].Value })
    if (-not $Ports.Count) {
        throw "Cannot identify the radio's physical USB port. Check its serial number and Windows device connection."
    }
    $HostPort = $Ports -join "."
    Write-Output "Passing through radio $RadioSerial on USB port $HostPort."
    $Arguments += @("-writable-system", "-usb-passthrough", "vendorid=0x239a,productid=0x00b3,hostport=$HostPort")
}
Start-Process -FilePath $Emulator -ArgumentList $Arguments `
    -RedirectStandardOutput (Join-Path $RepoRoot "dist/android/tablet-emulator.stdout.log") `
    -RedirectStandardError (Join-Path $RepoRoot "dist/android/tablet-emulator.stderr.log") `
    -PassThru | Select-Object Id,ProcessName
