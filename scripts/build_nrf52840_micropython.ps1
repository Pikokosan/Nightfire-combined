param(
    [string]$MicroPythonRoot = "C:\Users\hog28\OneDrive\Documents\NightfirePY\e77-micropython\micropython",
    [string]$Board = "NICENANO",
    [string]$BoardDir = "",
    [string]$Bootloader = "open_bootloader",
    [int]$BootloaderVersionMajor = 1,
    [int]$BootloaderVersionMinor = 2,
    [string]$ToolchainDir = "C:\Users\hog28\OneDrive\Documents\NightfirePY\e77-micropython\arm-toolchain\arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi\bin"
)

$ErrorActionPreference = "Stop"

function ConvertTo-WslPath([string]$WindowsPath) {
    $resolved = (Resolve-Path $WindowsPath).Path -replace '\\', '/'
    if ($resolved -match '^([A-Za-z]):/(.*)$') {
        return "/mnt/$($matches[1].ToLower())/$($matches[2])"
    }
    return $resolved
}

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$NrfPort = Join-Path $MicroPythonRoot "ports\nrf"
$UserModules = Join-Path $RepoRoot "nrf52840\native"
$UserModule = Join-Path $UserModules "nrf_radio"
if (-not $BoardDir) {
    $BoardDir = Join-Path $RepoRoot "nrf52840\boards\$Board"
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

$WslBuildDir = "$WslNrfPort/build-$Board"
$WslUf2 = "$WslBuildDir/firmware.uf2"
$WslHex = "$WslBuildDir/firmware.hex"

wsl bash -lc "export PATH=${WslToolchain}:/usr/bin:/bin; cd $WslNrfPort && make clean $BuildArgs && make $BuildArgs -j2 && python3 $WslMicroPythonRoot/tools/uf2conv.py -c -f 0xADA52840 -o $WslUf2 $WslHex"
if ($LASTEXITCODE -ne 0) {
    throw "nRF52840 MicroPython build failed with exit code $LASTEXITCODE"
}

$Uf2 = Join-Path $NrfPort "build-$Board\firmware.uf2"
if (Test-Path -LiteralPath $Uf2) {
    Write-Host "Built $Uf2"
}
else {
    Write-Host "Built firmware. UF2 was not generated automatically; check build-$Board for output files."
}
