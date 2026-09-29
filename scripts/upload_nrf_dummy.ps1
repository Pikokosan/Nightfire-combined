param(
    [string]$Port = "COM7",
    [int]$Baud = 115200,
    [string]$ConfigPath = "",
    [switch]$NoMpy
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Uploader = Join-Path $Root "scripts\raw_micropython_upload.py"
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

$BuildDir = Join-Path $Root "build\nrf_dummy_upload"
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

$UploadArgs = @(
    $Uploader,
    "--port", $Port,
    "--baud", "$Baud",
    "--delete", "main.py",
    "--delete", "nfcp.py",
    "--delete", "nfcp.mpy",
    "--delete", "module_ota.py",
    "--delete", "module_ota.mpy",
    "--delete", "lcd1602_i2c.py",
    "--delete", "lcd1602_i2c.mpy",
    "--delete", "nrf_dummy_main.py",
    "--delete", "nrf_dummy_main.mpy"
)

if ($ConfigPath) {
    $UploadArgs += @("--delete", "nrf_node_config.py")
}

if ($NoMpy) {
    $UploadArgs += @(
        ".\main\nrf_dummy_main.py:main.py",
        ".\main\nfcp.py:nfcp.py",
        ".\main\module_ota.py:module_ota.py",
        ".\main\lcd1602_i2c.py:lcd1602_i2c.py"
    )
}
else {
    $DummyMpy = Compile-Mpy ".\main\nrf_dummy_main.py" "nrf_dummy_main.mpy"
    $NfcpMpy = Compile-Mpy ".\main\nfcp.py" "nfcp.mpy"
    $OtaMpy = Compile-Mpy ".\main\module_ota.py" "module_ota.mpy"
    $LcdMpy = Compile-Mpy ".\main\lcd1602_i2c.py" "lcd1602_i2c.mpy"

    $UploadArgs += @(
        ".\main\nrf_dummy_boot.py:main.py",
        "$($DummyMpy):nrf_dummy_main.mpy",
        "$($NfcpMpy):nfcp.mpy",
        "$($OtaMpy):module_ota.mpy",
        "$($LcdMpy):lcd1602_i2c.mpy"
    )
}

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigPath):nrf_node_config.py")
}

Push-Location $Root
try {
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 dummy upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. nrf_dummy_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
