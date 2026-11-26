param(
    # Which image: radio (USB bridge, "Nightfire Radio"), field (field modules
    # and dummies, "Nightfire Field") or audio (audio box, "Nightfire Audio").
    # They differ only in the name the board shows. Not needed with -Uf2.
    [ValidateSet("", "radio", "field", "audio")]
    [string]$Device = "",
    # Defaults to the prebuilt image in firmware\nrf52840. Pass -FromBuild to
    # use a fresh build from build_nrf52840_micropython.ps1 instead.
    [string]$Uf2 = "",
    [switch]$FromBuild,
    [string]$MicroPythonRoot = "",
    [string]$Board = "NIGHTFIRE",
    [string]$BootloaderDrive = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

if (-not $Uf2) {
    if (-not $Device) {
        throw "Pass -Device radio, field or audio (or -Uf2 <file>)."
    }
    if ($FromBuild) {
        if (-not $MicroPythonRoot) {
            $MicroPythonRoot = $env:MICROPYTHON_ROOT
        }
        if (-not $MicroPythonRoot) {
            throw "Set -MicroPythonRoot or MICROPYTHON_ROOT to the MicroPython checkout."
        }
        $Uf2 = Join-Path $MicroPythonRoot "ports\nrf\build-$Board-$($Device.ToUpper())\firmware.uf2"
    }
    else {
        $Uf2 = Join-Path $RepoRoot "firmware\nrf52840\nightfire_$($Device)_micropython.uf2"
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

# A copy started the moment the drive appears can fail while Windows is still
# mounting it, leaving the board in the bootloader. Let the drive settle, and
# retry while it is still there. When the board takes the image it restarts
# and the drive vanishes, which Windows often reports as an error on the last
# write ("the volume ... has been externally altered"); that is success.
Start-Sleep -Seconds 2
$Target = Join-Path $BootloaderDrive "firmware.uf2"
for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
    try {
        Copy-Item -LiteralPath $Uf2 -Destination $Target -Force
    }
    catch {
        Write-Host "Copy attempt $Attempt reported: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 3
    # Not Join-Path/Test-Path: they throw once the drive letter is gone.
    if (-not [IO.File]::Exists([IO.Path]::Combine($BootloaderDrive, "INFO_UF2.TXT"))) {
        Write-Host "Copied $Uf2 to $BootloaderDrive. The board took it and is restarting."
        return
    }
    Write-Host "The bootloader drive is still there; copying again."
}
throw "The board stayed in the bootloader after 3 copies of $Uf2."
