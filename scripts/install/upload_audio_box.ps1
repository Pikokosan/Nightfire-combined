# Installs the audio box firmware (audio_box/) on a nice!nano over USB:
# audio_main.py from audio_box/main/ plus the shared radio code from main/.
# The board needs the Nightfire MicroPython image (firmware/nrf52840/).
param(
    [string]$Port = "COM7",
    [int]$Baud = 115200,
    [string]$ConfigPath = "",
    [switch]$NoMpy
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$Python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Uploader = Join-Path $Root "scripts\lib\raw_micropython_upload.py"
$MpyCross = "mpy-cross"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "py"
}

$MpyCrossCommand = Get-Command $MpyCross -ErrorAction SilentlyContinue
if ($MpyCrossCommand) {
    $MpyCross = $MpyCrossCommand.Source
}
elseif (-not $NoMpy) {
    throw "mpy-cross was not found. Install mpy-cross or rerun with -NoMpy."
}

$BuildDir = Join-Path $Root "build\audio_box_upload"
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

function Compile-Mpy {
    param(
        [string]$Source,
        [string]$OutputName
    )

    $OutPath = Join-Path $BuildDir $OutputName
    & $MpyCross -o $OutPath $Source
    if ($LASTEXITCODE -ne 0) {
        throw "mpy-cross failed for $Source"
    }
    return $OutPath
}

# Board file name -> source, for every file the box runs.
$Files = [ordered]@{
    "audio_main" = ".\audio_box\main\audio_main.py"
    "player_link" = ".\audio_box\main\player_link.py"
    "nfcp" = ".\main\nfcp.py"
    "module_ota" = ".\main\module_ota.py"
    "nrf_module_common" = ".\main\nrf_module_common.py"
    "mesh" = ".\main\mesh.py"
    "module_link" = ".\main\module_link.py"
}

# Remove both forms of every file (and other firmware's leftovers), so an
# old .py never shadows a new .mpy.
$UploadArgs = @($Uploader, "--port", $Port, "--baud", "$Baud", "--delete", "main.py")
foreach ($name in @($Files.Keys) + @("module_script", "module_lcd", "lcd1602_i2c", "nrf_dummy_main", "field_main")) {
    $UploadArgs += @("--delete", "$name.py", "--delete", "$name.mpy")
}

if ($ConfigPath) {
    $UploadArgs += @("--delete", "nrf_node_config.py")
}

# main.py only imports audio_main and calls its main().
$UploadArgs += @(".\audio_box\main\audio_boot.py:main.py")
if ($NoMpy) {
    foreach ($name in $Files.Keys) {
        $UploadArgs += @("$($Files[$name]):$name.py")
    }
}
else {
    foreach ($name in $Files.Keys) {
        $Mpy = Compile-Mpy $Files[$name] "$name.mpy"
        $UploadArgs += @("$($Mpy):$name.mpy")
    }
}

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigPath):nrf_node_config.py")
}

Push-Location $Root
try {
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Audio box upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. The audio box firmware is installed on $Port."
}
finally {
    Pop-Location
}
