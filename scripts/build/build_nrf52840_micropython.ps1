# Builds the Nightfire MicroPython image. The three images differ only in the
# name the board shows (REPL banner, USB device and serial port names):
#   RADIO "Nightfire Radio" (USB bridge), FIELD "Nightfire Field" (field
#   modules and dummies), AUDIO "Nightfire Audio" (audio box).
# Each lands in firmware/nrf52840/nightfire_<device>_micropython.uf2 and .hex.
param(
    [ValidateSet("all", "RADIO", "FIELD", "AUDIO")]
    [string]$Device = "all",
    [string]$MicroPythonRoot = "",
    [string]$Board = "NIGHTFIRE",
    [string]$BoardDir = "",
    [string]$Bootloader = "open_bootloader",
    [int]$BootloaderVersionMajor = 1,
    [int]$BootloaderVersionMinor = 2,
    [string]$ToolchainDir = ""
)

$ErrorActionPreference = "Stop"

if (-not $MicroPythonRoot) {
    $MicroPythonRoot = $env:MICROPYTHON_ROOT
}
# The checkout and toolchain have lived in nrf-micropython and, more recently,
# e77-micropython (MicroPython be9a819, the build on the current boards).
$Candidates = @(
    "C:\Users\hog28\OneDrive\Documents\NightfirePY\nrf-micropython",
    "C:\Users\hog28\OneDrive\Documents\NightfirePY\e77-micropython"
)
if (-not $MicroPythonRoot) {
    foreach ($candidate in $Candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate "micropython\ports\nrf")) {
            $MicroPythonRoot = Join-Path $candidate "micropython"
            break
        }
    }
}

if (-not $ToolchainDir) {
    $ToolchainDir = $env:ARM_TOOLCHAIN_DIR
}
if (-not $ToolchainDir) {
    foreach ($candidate in $Candidates) {
        $bin = Join-Path $candidate "arm-toolchain\arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi\bin"
        if (Test-Path -LiteralPath $bin) {
            $ToolchainDir = $bin
            break
        }
    }
}

function ConvertTo-WslPath([string]$WindowsPath) {
    $resolved = (Resolve-Path $WindowsPath).Path -replace '\\', '/'
    if ($resolved -match '^([A-Za-z]):/(.*)$') {
        return "/mnt/$($matches[1].ToLower())/$($matches[2])"
    }
    return $resolved
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$NrfPort = Join-Path $MicroPythonRoot "ports\nrf"
$UserModules = Join-Path $RepoRoot "firmware\micropython\native"
$UserModule = Join-Path $UserModules "nrf_radio"
if (-not $BoardDir) {
    $BoardDir = Join-Path $RepoRoot "firmware\micropython\boards\$Board"
}

if (-not (Test-Path -LiteralPath $NrfPort)) {
    throw "MicroPython nRF port was not found at $NrfPort"
}

if (-not (Test-Path -LiteralPath (Join-Path $UserModule "micropython.mk"))) {
    throw "nrf_radio MicroPython make fragment was not found at $UserModule"
}
if (-not (Test-Path -LiteralPath $BoardDir)) {
    throw "Board directory was not found at $BoardDir"
}

$WslMicroPythonRoot = ConvertTo-WslPath $MicroPythonRoot
$WslNrfPort = ConvertTo-WslPath $NrfPort
$WslUserModules = ConvertTo-WslPath $UserModules
$WslBoardDir = ConvertTo-WslPath $BoardDir
$WslToolchain = ConvertTo-WslPath $ToolchainDir
$BuildArgs = "BOARD=$Board BOARD_DIR=$WslBoardDir USER_C_MODULES=$WslUserModules"
if ($Bootloader) {
    $BuildArgs = "$BuildArgs BOOTLOADER=$Bootloader BOOTLOADER_VERSION_MAJOR=$BootloaderVersionMajor BOOTLOADER_VERSION_MINOR=$BootloaderVersionMinor"
}

$ArtifactDir = Join-Path $RepoRoot "firmware/nrf52840"
if (-not (Test-Path -LiteralPath $ArtifactDir)) {
    New-Item -ItemType Directory -Path $ArtifactDir | Out-Null
}

$Devices = if ($Device -eq "all") { @("RADIO", "FIELD", "AUDIO") } else { @($Device) }
foreach ($Dev in $Devices) {
    $BuildName = "build-$Board-$Dev"
    $WslBuildDir = "$WslNrfPort/$BuildName"
    $WslUf2 = "$WslBuildDir/firmware.uf2"
    $WslHex = "$WslBuildDir/firmware.hex"
    $DevArgs = "$BuildArgs NIGHTFIRE_DEVICE=$Dev BUILD=$BuildName"

    Write-Host "=== Building the $Dev image"
    wsl bash -lc "export PATH=${WslToolchain}:/usr/bin:/bin; cd $WslNrfPort && make clean $DevArgs && make $DevArgs -j2 && python3 $WslMicroPythonRoot/tools/uf2conv.py -c -f 0xADA52840 -o $WslUf2 $WslHex"
    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 MicroPython build ($Dev) failed with exit code $LASTEXITCODE"
    }

    $Uf2 = Join-Path $NrfPort "$BuildName/firmware.uf2"
    $Hex = Join-Path $NrfPort "$BuildName/firmware.hex"
    if (-not ((Test-Path -LiteralPath $Uf2) -and (Test-Path -LiteralPath $Hex))) {
        throw "Built $Dev, but $Uf2 or $Hex is missing."
    }
    $Name = "nightfire_$($Dev.ToLower())_micropython"
    Copy-Item -LiteralPath $Uf2 -Destination (Join-Path $ArtifactDir "$Name.uf2") -Force
    Copy-Item -LiteralPath $Hex -Destination (Join-Path $ArtifactDir "$Name.hex") -Force
    Write-Host "Copied $Name.uf2 and $Name.hex to $ArtifactDir"
}
