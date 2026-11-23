param(
    [Parameter(Mandatory = $true)]
    [string]$IconSource,

    [Parameter(Mandatory = $true)]
    [string]$SplashSource,

    [string]$OutputRoot
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $repoRoot 'design/simulator-branding/final'
}

$OutputRoot = [System.IO.Path]::GetFullPath($OutputRoot)
$iconRoot = Join-Path $OutputRoot 'icons'
$splashRoot = Join-Path $OutputRoot 'splash'
New-Item -ItemType Directory -Force -Path $iconRoot, $splashRoot | Out-Null

$iconMaster = Join-Path $OutputRoot 'nightfire-simulator-logo-master.png'
$splashMaster = Join-Path $OutputRoot 'nightfire-simulator-splash-master.png'
Copy-Item -LiteralPath $IconSource -Destination $iconMaster -Force
Copy-Item -LiteralPath $SplashSource -Destination $splashMaster -Force

function Save-ResizedImage {
    param(
        [System.Drawing.Image]$Image,
        [int]$Width,
        [int]$Height,
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $bitmap = New-Object System.Drawing.Bitmap $Width, $Height,
        ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality
            $graphics.DrawImage($Image, 0, 0, $Width, $Height)
        } finally {
            $graphics.Dispose()
        }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $bitmap.Dispose()
    }
}

function Write-PngIco {
    param(
        [System.Drawing.Image]$Image,
        [int[]]$Sizes,
        [string]$Path
    )

    $images = @()
    foreach ($size in $Sizes) {
        $bitmap = New-Object System.Drawing.Bitmap $size, $size,
            ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.DrawImage($Image, 0, 0, $size, $size)
        } finally {
            $graphics.Dispose()
        }

        $stream = New-Object System.IO.MemoryStream
        $bitmap.Save($stream, [System.Drawing.Imaging.ImageFormat]::Png)
        $bitmap.Dispose()
        $images += ,$stream.ToArray()
        $stream.Dispose()
    }

    $file = [System.IO.File]::Create($Path)
    $writer = New-Object System.IO.BinaryWriter $file
    try {
        $writer.Write([uint16]0)
        $writer.Write([uint16]1)
        $writer.Write([uint16]$Sizes.Count)
        $offset = 6 + (16 * $Sizes.Count)
        for ($index = 0; $index -lt $Sizes.Count; $index++) {
            $sizeByte = if ($Sizes[$index] -ge 256) { 0 } else { $Sizes[$index] }
            $writer.Write([byte]$sizeByte)
            $writer.Write([byte]$sizeByte)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([uint32]$images[$index].Length)
            $writer.Write([uint32]$offset)
            $offset += $images[$index].Length
        }
        foreach ($bytes in $images) {
            $writer.Write($bytes)
        }
    } finally {
        $writer.Dispose()
        $file.Dispose()
    }
}

$icon = [System.Drawing.Image]::FromFile($iconMaster)
try {
    foreach ($size in @(16, 20, 24, 29, 32, 40, 48, 58, 60, 64, 72, 76, 80, 87, 96, 120, 128, 144, 152, 167, 180, 192, 256, 512, 1024)) {
        Save-ResizedImage $icon $size $size (Join-Path $iconRoot "nightfire-simulator-$size.png")
    }
    Write-PngIco $icon @(16, 24, 32, 48, 64, 128, 256) (Join-Path $iconRoot 'nightfire-simulator.ico')
} finally {
    $icon.Dispose()
}

$splash = [System.Drawing.Image]::FromFile($splashMaster)
try {
    foreach ($size in @(
        @{ Width = 1280; Height = 720 },
        @{ Width = 1920; Height = 1080 },
        @{ Width = 2560; Height = 1440 }
    )) {
        $name = "nightfire-simulator-splash-$($size.Width)x$($size.Height).png"
        Save-ResizedImage $splash $size.Width $size.Height (Join-Path $splashRoot $name)
    }
} finally {
    $splash.Dispose()
}

Write-Host "Generated Nightfire Simulator branding in $OutputRoot"
