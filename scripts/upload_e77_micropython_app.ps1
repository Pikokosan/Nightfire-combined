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
    $compileFiles = @(
        @{ Source = "nfcp.py"; Target = "nfcp.mpy" },
        @{ Source = "e77_gfsk.py"; Target = "e77_gfsk.mpy" },
        @{ Source = "mc33996_driver.py"; Target = "mc33996_driver.mpy" },
        @{ Source = "e77_main.py"; Target = "main.mpy" }
    )

    foreach ($file in $compileFiles) {
        & mpy-cross -O3 (Join-Path $App $file.Source) -o (Join-Path $Deploy $file.Target)
        if ($LASTEXITCODE -ne 0) {
            throw "mpy-cross failed for $($file.Source)."
        }
    }

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
        "$Deploy\main.mpy:main.mpy" `
        "$Deploy\nfcp.mpy:nfcp.mpy" `
        "$Deploy\e77_gfsk.mpy:e77_gfsk.mpy" `
        "$Deploy\mc33996_driver.mpy:mc33996_driver.mpy" `
        "$Deploy\boot.py:boot.py"

    if ($LASTEXITCODE -ne 0) {
        throw "E77 MicroPython upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. e77_main.py is now installed as main.mpy and started by boot.py on $Port."
}
finally {
    Pop-Location
}
