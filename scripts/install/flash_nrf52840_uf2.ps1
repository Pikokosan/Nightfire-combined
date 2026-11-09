param(
    # Defaults to the prebuilt image in firmware\nrf52840. Pass -FromBuild to
    # use a fresh build from build_nrf52840_micropython.ps1 instead.
    [string]$Uf2 = "",
    [switch]$FromBuild,
    [string]$MicroPythonRoot = "",
    [string]$Board = "NICENANO",
    [string]$BootloaderDrive = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not $Uf2) {
    if ($FromBuild) {
        if (-not $MicroPythonRoot) {
            $MicroPythonRoot = $env:MICROPYTHON_ROOT
        }
        if (-not $MicroPythonRoot) {
            throw "Set -MicroPythonRoot or MICROPYTHON_ROOT to the MicroPython checkout."
        }
        $Uf2 = Join-Path $MicroPythonRoot "ports\nrf\build-$Board\firmware.uf2"
    }
    else {
        $Uf2 = Join-Path $RepoRoot "firmware\nrf52840\nightfire_nicenano_micropython.uf2"
    }
}

if (-not (Test-Path -LiteralPath $Uf2)) {
    throw "UF2 firmware was not found at $Uf2."
}

if (-not $BootloaderDrive) {
    # The nice!nano bootloader drive has an INFO_UF2.TXT at its root.
    $Drives = @(Get-PSDrive -PSProvider FileSystem | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.Root "INFO_UF2.TXT")
    })
    if ($Drives.Count -eq 0) {
        throw "No UF2 bootloader drive found. Double-tap the board's reset button (or run enter_nrf_bootloader.py) and try again."
    }
    if ($Drives.Count -gt 1) {
        throw "More than one UF2 bootloader drive found; pass -BootloaderDrive."
    }
    $BootloaderDrive = $Drives[0].Root
}

if (-not (Test-Path -LiteralPath $BootloaderDrive)) {
    throw "Bootloader drive $BootloaderDrive was not found. Put the nRF52840 board in UF2 bootloader mode."
}

Copy-Item -LiteralPath $Uf2 -Destination (Join-Path $BootloaderDrive "firmware.uf2") -Force
Write-Host "Copied $Uf2 to $BootloaderDrive. The board restarts by itself when the copy finishes."
