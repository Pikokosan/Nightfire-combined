param(
    [string]$Port = "COM15",
    [int]$Baud = 115200,
    [string]$ConfigPath = ".\main\nrf_bridge_config_master.py"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Uploader = Join-Path $Root "scripts\raw_micropython_upload.py"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "py"
}

$UploadArgs = @(
    $Uploader,
    "--port", $Port,
    "--baud", "$Baud",
    "--delete", "main.py",
    "--delete", "bridge_radio.py",
    "--delete", "nrf_bridge_config.py"
)

$UploadArgs += @(
    ".\main\nrf_bridge_main.py:main.py",
    ".\main\bridge_radio.py:bridge_radio.py"
)

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigPath):nrf_bridge_config.py")
}

Push-Location $Root
try {
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 bridge upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. nrf_bridge_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
