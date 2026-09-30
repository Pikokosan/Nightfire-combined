param(
    [string]$OutputDir = ".\dist\bridge_update"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$App = Join-Path $Root "main"
$Output = Join-Path $Root $OutputDir

$files = @(
    @{ Source = "nrf_bridge_main.py"; Target = "main.py" },
    @{ Source = "bridge_radio.py"; Target = "bridge_radio.py" },
    @{ Source = "nrf_bridge_config_master.py"; Target = "nrf_bridge_config.py" }
)

New-Item -ItemType Directory -Force $Output | Out-Null
Get-ChildItem -LiteralPath $Output -Force | Remove-Item -Recurse -Force

foreach ($file in $files) {
    $sourcePath = Join-Path $App $file.Source
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Missing source file: $sourcePath"
    }

    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $Output $file.Target) -Force
}

Write-Host "Done. Copy this folder to the master SD card as:"
Write-Host "\bridge_update"
Write-Host ""
Write-Host "Source folder:"
Write-Host $Output
