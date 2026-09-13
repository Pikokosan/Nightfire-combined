param(
    [ValidateSet("slave", "dummy", "nrf-dummy")]
    [string]$Target = "slave",
    [string]$Version = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$App = Join-Path $Root "main"

if (-not $OutputDir) {
    $OutputDir = ".\dist\${Target}_ota_package"
}

$Output = Join-Path $Root $OutputDir

$entry = switch ($Target) {
    "slave" { "slave_main.py" }
    "dummy" { "dummy_main.py" }
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
    @{ Source = "module_ota.py"; Target = "/module_ota.py" }
)

if ($Target -eq "slave") {
    $files += @(
        @{ Source = "e77_radio.py"; Target = "/e77_radio.py" },
        @{ Source = "module_transport.py"; Target = "/module_transport.py" },
        @{ Source = "e77_bootloader.py"; Target = "/e77_bootloader.py" },
        @{ Source = "mc33996_driver.py"; Target = "/mc33996_driver.py" }
    )
}
elseif ($Target -eq "dummy") {
    $files += @(
        @{ Source = "e77_radio.py"; Target = "/e77_radio.py" },
        @{ Source = "module_transport.py"; Target = "/module_transport.py" },
        @{ Source = "e77_bootloader.py"; Target = "/e77_bootloader.py" }
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

$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding ASCII (Join-Path $Output "e77_update.json")

Write-Host "Done. Copy the contents of this folder to the master SD card root:"
Write-Host $Output
