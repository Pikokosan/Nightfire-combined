param(
    [string]$Port = "COM5"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = "C:\Users\hog28\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$sitePackages = "C:\Users\hog28\.micropython-studio\.venv\Lib\site-packages"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python was not found at: $python"
}

Push-Location $repoRoot
try {
    $env:PYTHONPATH = $sitePackages
    Write-Host "Uploading standalone Trellis test to $Port..."

    & $python ".\scripts\lib\raw_micropython_upload.py" `
        --port $Port `
        ".\main\trellis_hardware_test.py:main.py" `
        ".\main\adafruit_trellis.py:adafruit_trellis.py"

    if ($LASTEXITCODE -ne 0) {
        throw "Trellis test upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. The Trellis hardware test is running on $Port."
    Write-Host "Restore the command firmware with: .\scripts\install\upload_esp32_master.ps1 -Port $Port"
}
finally {
    Pop-Location
}
