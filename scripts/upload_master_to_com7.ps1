param(
    [string]$Port = "COM7"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python was not found at: $python"
}

Push-Location $repoRoot
try {
    Write-Host "Uploading master controller files to $Port..."

    & $python ".\scripts\raw_micropython_upload.py" `
        --port $Port `
        ".\main\master_main.py:main.py" `
        ".\main\nfcp.py:nfcp.py" `
        ".\main\display_driver.py:display_driver.py" `
        ".\main\sdcard_driver.py:sdcard_driver.py" `
        ".\main\ch422g.py:ch422g.py"

    if ($LASTEXITCODE -ne 0) {
        throw "Master upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. master_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
