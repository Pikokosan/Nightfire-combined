param(
    [string]$Port = "COM17"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Uploader = Join-Path $Root "scripts\raw_micropython_upload.py"
$App = Join-Path $Root "main"
$Deploy = Join-Path $Root "dist\e77_micropython_deploy"

New-Item -ItemType Directory -Force $Deploy | Out-Null
Remove-Item -Force "$Deploy\*" -ErrorAction SilentlyContinue
mpy-cross "$App\nfcp.py" -o "$Deploy\nfcp.mpy"
mpy-cross "$App\e77_gfsk.py" -o "$Deploy\e77_gfsk.mpy"
mpy-cross "$App\e77_main.py" -o "$Deploy\e77_main.mpy"
Set-Content -NoNewline -Encoding ASCII "$Deploy\main.py" "import e77_main`n"

py -3 $Uploader --port $Port --baud 115200 `
    --delete "nfcp.py" `
    --delete "nfcp.mpy" `
    --delete "e77_gfsk.py" `
    --delete "e77_gfsk.mpy" `
    --delete "e77_main.py" `
    --delete "e77_main.mpy" `
    --delete "main.py" `
    "$Deploy\nfcp.mpy:nfcp.mpy" `
    "$Deploy\e77_gfsk.mpy:e77_gfsk.mpy" `
    "$Deploy\e77_main.mpy:e77_main.mpy" `
    "$Deploy\main.py:main.py"
