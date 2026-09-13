param(
    [string]$MicroPythonRoot = "C:\Users\hog28\OneDrive\Documents\NightfirePY\e77-micropython\micropython",
    [string]$Board = "NICENANO",
    [string]$BootloaderDrive = "F:"
)

$ErrorActionPreference = "Stop"

$Uf2 = Join-Path $MicroPythonRoot "ports\nrf\build-$Board\firmware.uf2"

if (-not (Test-Path -LiteralPath $Uf2)) {
    throw "UF2 firmware was not found at $Uf2. Run scripts\build_nrf52840_micropython.ps1 first."
}

if (-not (Test-Path -LiteralPath $BootloaderDrive)) {
    throw "Bootloader drive $BootloaderDrive was not found. Put the nRF52840 board in UF2 bootloader mode."
}

Copy-Item -LiteralPath $Uf2 -Destination (Join-Path $BootloaderDrive "firmware.uf2") -Force
Write-Host "Copied $Uf2 to $BootloaderDrive"
