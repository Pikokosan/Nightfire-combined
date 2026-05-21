param(
    [string]$Port = "COM11"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$mpremote = "C:\Users\hog28\.micropython-studio\.venv\Scripts\mpremote.exe"

if (-not (Test-Path -LiteralPath $mpremote)) {
    throw "mpremote was not found at: $mpremote"
}

Push-Location $repoRoot
try {
    Write-Host "Uploading dummy device files to $Port..."

    & $mpremote connect $Port fs cp ".\main\dummy_main.py" ":main.py"
    & $mpremote connect $Port fs cp ".\main\nfcp.py" ":nfcp.py"

    Write-Host "Resetting device..."
    & $mpremote connect $Port reset

    Write-Host "Done. dummy_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
