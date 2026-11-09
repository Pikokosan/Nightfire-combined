param(
    [ValidateSet("field", "nrf-dummy")]
    [string]$Target = "field",
    [string]$Version = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$App = Join-Path $Root "main"

if (-not $OutputDir) {
    $OutputDir = ".\dist\${Target}_ota_package"
}

$Output = Join-Path $Root $OutputDir

$entry = switch ($Target) {
    "field" { "field_main.py" }
    "nrf-dummy" { "nrf_dummy_main.py" }
}

if (-not $Version) {
    $versionLine = Select-String -Path (Join-Path $App $entry) -Pattern 'APP_FIRMWARE_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($versionLine -and $versionLine.Matches.Count -gt 0) {
        $Version = $versionLine.Matches[0].Groups[1].Value
    }
    else {
        $Version = "unknown"
    }
}

$files = @(
    @{ Source = $entry; Target = "/main.py" },
    @{ Source = "nfcp.py"; Target = "/nfcp.py" },
    @{ Source = "module_ota.py"; Target = "/module_ota.py" },
    @{ Source = "nrf_module_common.py"; Target = "/nrf_module_common.py" },
    @{ Source = "mesh.py"; Target = "/mesh.py" },
    @{ Source = "module_link.py"; Target = "/module_link.py" },
    @{ Source = "module_script.py"; Target = "/module_script.py" },
    @{ Source = "module_lcd.py"; Target = "/module_lcd.py" },
    @{ Source = "lcd1602_i2c.py"; Target = "/lcd1602_i2c.py" }
)

if ($Target -eq "field") {
    $files += @(
        @{ Source = "shift_register74hc595.py"; Target = "/shift_register74hc595.py" },
        @{ Source = "analog_mux74hc4051.py"; Target = "/analog_mux74hc4051.py" },
        @{ Source = "adc_scaling.py"; Target = "/adc_scaling.py" },
        @{ Source = "field_measurement.py"; Target = "/field_measurement.py" }
    )
}

New-Item -ItemType Directory -Force $Output | Out-Null
Get-ChildItem -LiteralPath $Output -Force | Remove-Item -Recurse -Force

$manifestFiles = @()
foreach ($file in $files) {
    $sourcePath = Join-Path $App $file.Source
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Missing source file: $sourcePath"
    }

    Copy-Item -LiteralPath $sourcePath -Destination (Join-Path $Output $file.Source) -Force
    $manifestFiles += [ordered]@{
        source = $file.Source
        target = $file.Target
    }
}

$manifest = [ordered]@{
    package_name = "Nightfire Module OTA - $Target"
    target = $Target
    version = $Version
    files = $manifestFiles
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding ASCII (Join-Path $Output "ota_update.json")

Write-Host "Done. Copy the contents of this folder to the master SD card root:"
Write-Host $Output
