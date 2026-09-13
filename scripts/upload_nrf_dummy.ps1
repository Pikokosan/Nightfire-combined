param(
    [string]$Port = "COM7",
    [int]$Baud = 115200,
    [string]$ConfigPath = ""
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
    "--delete", "nfcp.py",
    "--delete", "module_ota.py"
)

if ($ConfigPath) {
    $UploadArgs += @("--delete", "nrf_node_config.py")
}

$UploadArgs += @(
    ".\main\nrf_dummy_main.py:main.py",
    ".\main\nfcp.py:nfcp.py",
    ".\main\module_ota.py:module_ota.py"
)

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
