param(
    [string]$Port = "COM11"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$mpremote = "C:\Users\hog28\.micropython-studio\.venv\Scripts\mpremote.exe"

if (-not (Test-Path -LiteralPath $mpremote)) {
    throw "mpremote was not found at: $mpremote"
}

function Invoke-MpRemote {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $mpremote @Arguments

    if ($LASTEXITCODE -ne 0) {
        throw "mpremote failed with exit code $LASTEXITCODE while running: mpremote $($Arguments -join ' ')"
    }
}

Push-Location $repoRoot
try {
    Write-Host "Uploading dummy device files to $Port..."

    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\dummy_main.py", ":main.py")
    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\nfcp.py", ":nfcp.py")
    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\module_ota.py", ":module_ota.py")
    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\e77_radio.py", ":e77_radio.py")
    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\module_transport.py", ":module_transport.py")
    Invoke-MpRemote @("connect", $Port, "fs", "cp", ".\main\e77_bootloader.py", ":e77_bootloader.py")

    Write-Host "Resetting device..."
    Invoke-MpRemote @("connect", $Port, "reset")

    Write-Host "Done. dummy_main.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
