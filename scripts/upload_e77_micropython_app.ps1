param(
    [string]$Port = "COM17",
    [int]$Baud = 115200
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Uploader = Join-Path $Root "scripts\raw_micropython_upload.py"
$App = Join-Path $Root "main"
$Deploy = Join-Path $Root "dist\e77_micropython_deploy"

if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "py"
}

if (-not (Test-Path -LiteralPath $Uploader)) {
    throw "Could not find raw uploader: $Uploader"
}

Push-Location $Root
try {
    New-Item -ItemType Directory -Force $Deploy | Out-Null
    Remove-Item -Force (Join-Path $Deploy "*") -ErrorAction SilentlyContinue

    Write-Host "Compiling E77 MicroPython files to .mpy..."
    & mpy-cross (Join-Path $App "nfcp.py") -o (Join-Path $Deploy "nfcp.mpy")
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed for nfcp.py." }

    & mpy-cross (Join-Path $App "e77_gfsk.py") -o (Join-Path $Deploy "e77_gfsk.mpy")
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed for e77_gfsk.py." }

    & mpy-cross (Join-Path $App "mc33996_driver.py") -o (Join-Path $Deploy "mc33996_driver.mpy")
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed for mc33996_driver.py." }

    & mpy-cross (Join-Path $App "e77_main.py") -o (Join-Path $Deploy "main.mpy")
    if ($LASTEXITCODE -ne 0) { throw "mpy-cross failed for e77_main.py." }

    Set-Content -NoNewline -Encoding ASCII (Join-Path $Deploy "boot.py") "import main`n"

    Write-Host "Uploading compiled E77 MicroPython app to $Port..."
    & $Python $Uploader --port $Port --baud $Baud `
        --delete "boot.py" `
        --delete "main.py" `
        --delete "main.mpy" `
        --delete "e77_main.py" `
        --delete "e77_main.mpy" `
        --delete "e77_gfsk.py" `
        --delete "e77_gfsk.mpy" `
        --delete "mc33996_driver.py" `
        --delete "mc33996_driver.mpy" `
        --delete "nfcp.py" `
        --delete "nfcp.mpy" `
        "$Deploy\nfcp.mpy:nfcp.mpy" `
        "$Deploy\e77_gfsk.mpy:e77_gfsk.mpy" `
        "$Deploy\mc33996_driver.mpy:mc33996_driver.mpy" `
        "$Deploy\main.mpy:main.mpy" `
        "$Deploy\boot.py:boot.py"

    if ($LASTEXITCODE -ne 0) {
        throw "E77 MicroPython upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. e77_main.py is now installed as main.mpy and started by boot.py on $Port."
}
finally {
    Pop-Location
}
