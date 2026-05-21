param(
    [string]$Port = "COM10"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$mpremote = "C:\Users\hog28\.micropython-studio\.venv\Scripts\mpremote.exe"

if (-not (Test-Path -LiteralPath $mpremote)) {
    throw "mpremote was not found at: $mpremote"
}

Push-Location $repoRoot
try {
    Write-Host "Uploading slave device files to $Port..."

    & $mpremote connect $Port fs cp ".\main\slave_main.py" ":main.py"
    & $mpremote connect $Port fs cp ".\main\nfcp.py" ":nfcp.py"
    & $mpremote connect $Port fs cp ".\main\mc33996_driver.py" ":mc33996_driver.py"

    Write-Host "Resetting device..."
    & $mpremote connect $Port reset

    Write-Host "Done. slave_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
