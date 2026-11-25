# Installs the audio box player (audio_box/player/) on an ESP32 running stock
# MicroPython: player_main.py, its pin config and a main.py that starts it.
# Tracks go on the player's SD card by hand (NNN.wav, 16-bit stereo).
param(
    [string]$Port = "COM7",
    [string]$ConfigPath = ".\audio_box\player\player_config.py"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$Python = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $Python = "py"
}

Push-Location $Root
try {
    & $Python ".\scripts\lib\raw_micropython_upload.py" `
        --port $Port `
        --delete "main.py" `
        ".\audio_box\player\player_main.py:player_main.py" `
        "$($ConfigPath):player_config.py" `
        ".\audio_box\player\player_boot.py:main.py"

    if ($LASTEXITCODE -ne 0) {
        throw "Audio player upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. The audio player is installed on $Port."
}
finally {
    Pop-Location
}
