param(
    [string]$Version = "",
    [string]$OutputDir = ".\dist\e77_ota_package",
    [switch]$HardwareSafetyOff
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

$CompileApp = $App
if ($HardwareSafetyOff) {
    $CompileApp = Join-Path $Output "source"
    New-Item -ItemType Directory -Force $CompileApp | Out-Null
    Copy-Item -Force (Join-Path $App "nfcp.py") (Join-Path $CompileApp "nfcp.py")
    Copy-Item -Force (Join-Path $App "e77_gfsk.py") (Join-Path $CompileApp "e77_gfsk.py")
    Copy-Item -Force (Join-Path $App "mc33996_driver.py") (Join-Path $CompileApp "mc33996_driver.py")

    $e77Source = Get-Content -Raw -Path (Join-Path $App "e77_main.py")
    $e77Source = $e77Source -replace 'HARDWARE_SAFETY_ENABLED\s*=\s*True', 'HARDWARE_SAFETY_ENABLED = False'
    Set-Content -NoNewline -Encoding ASCII -Path (Join-Path $CompileApp "e77_main.py") -Value $e77Source
    Write-Host "Hardware safety is OFF for this OTA package."
}

Write-Host "Compiling E77 MicroPython OTA package version $Version..."

$compileFiles = @(
    @{ Source = "nfcp.py"; Target = "nfcp.mpy" },
    @{ Source = "e77_gfsk.py"; Target = "e77_gfsk.mpy" },
    @{ Source = "mc33996_driver.py"; Target = "mc33996_driver.mpy" },
    @{ Source = "e77_main.py"; Target = "main.mpy" }
)

foreach ($file in $compileFiles) {
    & mpy-cross -O3 (Join-Path $CompileApp $file.Source) -o (Join-Path $Output $file.Target)
    if ($LASTEXITCODE -ne 0) {
        throw "mpy-cross failed for $($file.Source)."
    }
}

if ($HardwareSafetyOff) {
    Remove-Item -LiteralPath $CompileApp -Recurse -Force -ErrorAction SilentlyContinue
}

Set-Content -NoNewline -Encoding ASCII (Join-Path $Output "boot.py") "import main`n"

$manifest = [ordered]@{
    package_name = "Nightfire E77 MicroPython OTA"
    version = $Version
    hardware_safety_enabled = -not [bool]$HardwareSafetyOff
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
