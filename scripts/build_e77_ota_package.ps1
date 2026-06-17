param(
    [string]$Version = "",
    [string]$OutputDir = ".\dist\e77_ota_package"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$App = Join-Path $Root "main"
$Output = Join-Path $Root $OutputDir

if (-not $Version) {
    $versionLine = Select-String -Path (Join-Path $App "e77_main.py") -Pattern 'APP_FIRMWARE_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($versionLine -and $versionLine.Matches.Count -gt 0) {
        $Version = $versionLine.Matches[0].Groups[1].Value
    }
    else {
        $Version = "unknown"
    }
}

New-Item -ItemType Directory -Force $Output | Out-Null
Remove-Item -LiteralPath (Join-Path $Output "*") -Force -ErrorAction SilentlyContinue

Write-Host "Compiling E77 MicroPython OTA package version $Version..."

$compileFiles = @(
    @{ Source = "nfcp.py"; Target = "nfcp.mpy" },
    @{ Source = "e77_gfsk.py"; Target = "e77_gfsk.mpy" },
    @{ Source = "mc33996_driver.py"; Target = "mc33996_driver.mpy" },
    @{ Source = "e77_main.py"; Target = "main.mpy" }
)

foreach ($file in $compileFiles) {
    & mpy-cross -O3 (Join-Path $App $file.Source) -o (Join-Path $Output $file.Target)
    if ($LASTEXITCODE -ne 0) {
        throw "mpy-cross failed for $($file.Source)."
    }
}

Set-Content -NoNewline -Encoding ASCII (Join-Path $Output "boot.py") "import main`n"

$manifest = [ordered]@{
    package_name = "Nightfire E77 MicroPython OTA"
    version = $Version
    files = @(
        [ordered]@{ source = "main.mpy"; target = "/main.mpy" },
        [ordered]@{ source = "nfcp.mpy"; target = "/nfcp.mpy" },
        [ordered]@{ source = "e77_gfsk.mpy"; target = "/e77_gfsk.mpy" },
        [ordered]@{ source = "mc33996_driver.mpy"; target = "/mc33996_driver.mpy" },
        [ordered]@{ source = "boot.py"; target = "/boot.py" }
    )
}

$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding ASCII (Join-Path $Output "e77_update.json")

Write-Host "Done. Copy the contents of this folder to the master SD card root:"
Write-Host $Output
