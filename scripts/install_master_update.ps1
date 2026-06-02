param(
    [string]$Port = "COM7",
    [string]$PackageDir = ""
)

$ErrorActionPreference = "Stop"

function Find-Python {
    $preferred = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $preferred) {
        return $preferred
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        return $python.Source
    }

    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        return $py.Source
    }

    throw "Python was not found. Install Python with pyserial, or use the MicroPython Studio environment."
}

function Resolve-PackageDir {
    param([string]$Requested)

    if ($Requested) {
        return (Resolve-Path $Requested).Path
    }

    $scriptDir = $PSScriptRoot
    if (Test-Path (Join-Path $scriptDir "master_update\master_update.json")) {
        return $scriptDir
    }

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $defaultPackage = Join-Path $repoRoot "dist\nightfire_update_package"
    if (Test-Path (Join-Path $defaultPackage "master_update\master_update.json")) {
        return $defaultPackage
    }

    throw "Could not find master_update\master_update.json. Pass -PackageDir."
}

function Resolve-Uploader {
    param([string]$PackageRoot)

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $repoUploader = Join-Path $repoRoot "scripts\raw_micropython_upload.py"
    if (Test-Path -LiteralPath $repoUploader) {
        return $repoUploader
    }

    $packagedUploader = Join-Path $PackageRoot "raw_micropython_upload.py"
    if (Test-Path -LiteralPath $packagedUploader) {
        return $packagedUploader
    }

    throw "Could not find raw_micropython_upload.py."
}

$packageRoot = Resolve-PackageDir $PackageDir
$manifestPath = Join-Path $packageRoot "master_update\master_update.json"
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$python = Find-Python
$uploader = Resolve-Uploader $packageRoot

$copies = @()
foreach ($file in $manifest.files) {
    $source = Join-Path (Join-Path $packageRoot "master_update") $file.source
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Missing master update file: $source"
    }
    $target = [string]$file.target
    if ($target.StartsWith("/")) {
        $target = $target.Substring(1)
    }
    $copies += ("{0}:{1}" -f $source, $target)
}

Write-Host "Installing Nightfire master update $($manifest.package_version) to $Port..."
& $python $uploader --port $Port @copies

if ($LASTEXITCODE -ne 0) {
    throw "Master update failed with exit code $LASTEXITCODE."
}

Write-Host "Master update complete."
