param(
    [string]$Version = "0.2.1",
    [string]$OutputDir = ".\dist\nightfire_update_package",
    [string]$RadioFirmware = "..\..\Nightfire-E77\build\release\e77_radio.bin",
    [string]$RadioVersion = "0.2.5"
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")
$output = Join-Path $root $OutputDir
$masterOutput = Join-Path $output "master_update"
$zipPath = Join-Path $root ".\dist\nightfire_update_$Version.zip"
$radioFirmwarePath = Join-Path $root $RadioFirmware

if (Test-Path $output) {
    Remove-Item -LiteralPath $output -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $output | Out-Null
New-Item -ItemType Directory -Force -Path $masterOutput | Out-Null

$moduleFiles = @(
    @{ Source = "main\slave_main.py";       PackageName = "slave_main.py";       Target = "/main.py";             DeviceType = "0x20" },
    @{ Source = "main\dummy_main.py";       PackageName = "dummy_main.py";       Target = "/main.py";             DeviceType = "0x10" },
    @{ Source = "main\nfcp.py";             PackageName = "nfcp.py";             Target = "/nfcp.py" },
    @{ Source = "main\e77_radio.py";        PackageName = "e77_radio.py";        Target = "/e77_radio.py" },
    @{ Source = "main\e77_bootloader.py";   PackageName = "e77_bootloader.py";   Target = "/e77_bootloader.py" },
    @{ Source = "main\module_transport.py"; PackageName = "module_transport.py"; Target = "/module_transport.py" },
    @{ Source = "main\module_ota.py";       PackageName = "module_ota.py";       Target = "/module_ota.py" },
    @{ Source = "main\mc33996_driver.py";   PackageName = "mc33996_driver.py";   Target = "/mc33996_driver.py";   DeviceType = "0x20" }
)

$masterFiles = @(
    @{ Source = "main\master_main.py";    PackageName = "master_main.py";    Target = "/main.py" },
    @{ Source = "main\nfcp.py";           PackageName = "nfcp.py";           Target = "/nfcp.py" },
    @{ Source = "main\e77_radio.py";      PackageName = "e77_radio.py";      Target = "/e77_radio.py" },
    @{ Source = "main\e77_bootloader.py"; PackageName = "e77_bootloader.py"; Target = "/e77_bootloader.py" },
    @{ Source = "main\adafruit_trellis.py"; PackageName = "adafruit_trellis.py"; Target = "/adafruit_trellis.py" },
    @{ Source = "main\display_driver.py"; PackageName = "display_driver.py"; Target = "/display_driver.py" },
    @{ Source = "main\sdcard_driver.py";  PackageName = "sdcard_driver.py";  Target = "/sdcard_driver.py" },
    @{ Source = "main\ch422g.py";         PackageName = "ch422g.py";         Target = "/ch422g.py" },
    @{ Source = "assets\fonts\timer_96.bin"; PackageName = "timer_96.bin";   Target = "/timer_96.bin" }
)

$manifestFiles = @()
$masterManifestFiles = @()

if (-not ("Nightfire.Crc32" -as [type])) {
    Add-Type -TypeDefinition @"
namespace Nightfire {
    public static class Crc32 {
        public static uint Compute(string path) {
            uint crc = 0xFFFFFFFFu;
            byte[] buffer = new byte[4096];

            using (var stream = System.IO.File.OpenRead(path)) {
                int count;
                while ((count = stream.Read(buffer, 0, buffer.Length)) > 0) {
                    for (int i = 0; i < count; i++) {
                        crc ^= buffer[i];
                        for (int bit = 0; bit < 8; bit++) {
                            if ((crc & 1u) != 0u) {
                                crc = (crc >> 1) ^ 0xEDB88320u;
                            } else {
                                crc >>= 1;
                            }
                        }
                    }
                }
            }

            return crc ^ 0xFFFFFFFFu;
        }
    }
}
"@
}

function Get-FileCrc32 {
    param([string]$Path)

    return [Nightfire.Crc32]::Compute($Path)
}

foreach ($file in $moduleFiles) {
    $source = Join-Path $root $file.Source
    if (-not (Test-Path $source)) {
        throw "Missing package source: $($file.Source)"
    }

    Copy-Item -LiteralPath $source -Destination (Join-Path $output $file.PackageName)

    $entry = [ordered]@{
        source = $file.PackageName
        target = $file.Target
    }

    if ($file.ContainsKey("DeviceType")) {
        $entry.device_type = $file.DeviceType
    }

    $manifestFiles += $entry
}

foreach ($file in $masterFiles) {
    $source = Join-Path $root $file.Source
    if (-not (Test-Path $source)) {
        throw "Missing master package source: $($file.Source)"
    }

    Copy-Item -LiteralPath $source -Destination (Join-Path $masterOutput $file.PackageName)

    $masterManifestFiles += [ordered]@{
        source = $file.PackageName
        target = $file.Target
    }
}

if (Test-Path $radioFirmwarePath) {
    $radioPackageName = "e77_radio.bin"
    $radioCrc32 = Get-FileCrc32 $radioFirmwarePath
    Copy-Item -LiteralPath $radioFirmwarePath -Destination (Join-Path $output $radioPackageName)

    $manifestFiles += [ordered]@{
        source = $radioPackageName
        target = "/e77_radio.bin"
    }

    $manifestFiles += [ordered]@{
        source = "latest.json"
        target = "/latest.json"
    }

    $manifestFiles += [ordered]@{
        source = "radio_update.flag"
        target = "/radio_update.flag"
    }
} else {
    Write-Warning "Radio firmware not found, package will not include radio update files: $radioFirmwarePath"
}

$manifest = [ordered]@{
    package_name = "Nightfire module OTA"
    package_version = $Version
    radio_version = if (Test-Path $radioFirmwarePath) { $RadioVersion } else { "" }
    generated_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    files = $manifestFiles
}

$manifestJson = $manifest | ConvertTo-Json -Depth 5
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $output "module_ota.json"),
    $manifestJson + [Environment]::NewLine,
    $utf8NoBom
)

$masterManifest = [ordered]@{
    package_name = "Nightfire master update"
    package_version = $Version
    generated_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
    files = $masterManifestFiles
}

$masterManifestJson = $masterManifest | ConvertTo-Json -Depth 5
[System.IO.File]::WriteAllText(
    (Join-Path $masterOutput "master_update.json"),
    $masterManifestJson + [Environment]::NewLine,
    $utf8NoBom
)

Copy-Item `
    -LiteralPath (Join-Path $root "scripts\install_master_update.ps1") `
    -Destination (Join-Path $output "install_master_update.ps1")

Copy-Item `
    -LiteralPath (Join-Path $root "scripts\raw_micropython_upload.py") `
    -Destination (Join-Path $output "raw_micropython_upload.py")

if (Test-Path $radioFirmwarePath) {
    $radioManifest = [ordered]@{
        filename = "e77_radio.bin"
        version = $RadioVersion
        address = "0x08000000"
        crc32 = ("0x{0:X8}" -f $radioCrc32)
    } | ConvertTo-Json -Depth 3

    [System.IO.File]::WriteAllText(
        (Join-Path $output "latest.json"),
        $radioManifest + [Environment]::NewLine,
        $utf8NoBom
    )

    [System.IO.File]::WriteAllText(
        (Join-Path $output "radio_update.flag"),
        "update" + [Environment]::NewLine,
        $utf8NoBom
    )
}

$readme = @"
Nightfire Update Package $Version

This package contains both:
- master_update/: files used to update the master controller over USB
- module OTA files at the package root for the master GUI to send to modules
- install_master_update.ps1 and raw_micropython_upload.py for the PC updater

To update the master controller from a PC:
1. Connect the master device over USB.
2. Run:
   powershell -ExecutionPolicy Bypass -File .\install_master_update.ps1 -Port COM7

To update slave/dummy modules:
1. Copy the package root files to the root of the master device SD card.
   Keep module_ota.json, e77_radio.bin, latest.json, and radio_update.flag
   at the SD card root when present.
1. Discover or refresh modules.
2. Open Settings.
3. Tap MODULE OTA.
4. Tap START OTA.
5. Reset updated modules after main.py is replaced.

If this package includes e77_radio.bin, latest.json, and radio_update.flag,
the module will update its attached E77 radio on the next module boot before
returning to normal operation.
"@

[System.IO.File]::WriteAllText(
    (Join-Path $output "README.txt"),
    $readme + [Environment]::NewLine,
    $utf8NoBom
)

if (Test-Path $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Compress-Archive -Path (Join-Path $output "*") -DestinationPath $zipPath -Force

Write-Host "Nightfire update package:"
Write-Host "  Folder: $output"
Write-Host "  Zip:    $zipPath"
