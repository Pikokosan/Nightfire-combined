param(
    [Parameter(Mandatory = $true)]
    [string]$Source
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

$repoRoot = Split-Path -Parent $PSScriptRoot
$appRoot = Join-Path $repoRoot 'apps/nightfire_controller'
$brandingRoot = Join-Path $appRoot 'assets/branding'
$masterPath = Join-Path $brandingRoot 'nightfire-icon-master.png'

New-Item -ItemType Directory -Force -Path $brandingRoot | Out-Null
Copy-Item -LiteralPath $Source -Destination $masterPath -Force

function Save-ResizedPng {
    param(
        [System.Drawing.Image]$Image,
        [int]$Size,
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
    $bitmap = New-Object System.Drawing.Bitmap $Size, $Size,
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
            $graphics.DrawImage($Image, 0, 0, $Size, $Size)
        } finally {
            $graphics.Dispose()
        }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $bitmap.Dispose()
    }
}

function New-ForegroundImage {
    param([System.Drawing.Bitmap]$Master)

    $transparent = New-Object System.Drawing.Bitmap $Master.Width, $Master.Height,
        ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $minX = $Master.Width
    $minY = $Master.Height
    $maxX = -1
    $maxY = -1

    for ($y = 0; $y -lt $Master.Height; $y++) {
        for ($x = 0; $x -lt $Master.Width; $x++) {
            $pixel = $Master.GetPixel($x, $y)
            $brightness = [Math]::Max($pixel.R, [Math]::Max($pixel.G, $pixel.B))
            if ($brightness -gt 70) {
                $transparent.SetPixel($x, $y, $pixel)
                if ($x -lt $minX) { $minX = $x }
                if ($y -lt $minY) { $minY = $y }
                if ($x -gt $maxX) { $maxX = $x }
                if ($y -gt $maxY) { $maxY = $y }
            }
        }
    }

    if ($maxX -lt $minX -or $maxY -lt $minY) {
        $transparent.Dispose()
        throw 'Could not isolate the NF foreground from the icon background.'
    }

    $crop = New-Object System.Drawing.Bitmap ($maxX - $minX + 1), ($maxY - $minY + 1),
        ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($crop)
    try {
        $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
        $sourceRect = New-Object System.Drawing.Rectangle $minX, $minY, $crop.Width, $crop.Height
        $destinationRect = New-Object System.Drawing.Rectangle 0, 0, $crop.Width, $crop.Height
        $graphics.DrawImage($transparent, $destinationRect, $sourceRect, [System.Drawing.GraphicsUnit]::Pixel)
    } finally {
        $graphics.Dispose()
        $transparent.Dispose()
    }
    return $crop
}

function Save-AdaptiveForeground {
    param(
        [System.Drawing.Image]$Foreground,
        [int]$Size,
        [string]$Path
    )

    $canvas = New-Object System.Drawing.Bitmap $Size, $Size,
        ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($canvas)
        try {
            $graphics.Clear([System.Drawing.Color]::Transparent)
            $graphics.CompositingMode = [System.Drawing.Drawing2D.CompositingMode]::SourceCopy
            $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
            $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
            $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
            $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::HighQuality

            # Android's adaptive-icon guaranteed safe zone is 66dp inside a 108dp canvas.
            $targetWidth = [int][Math]::Round($Size * 0.60)
            $targetHeight = [int][Math]::Round($targetWidth * $Foreground.Height / $Foreground.Width)
            if ($targetHeight -gt [int][Math]::Round($Size * 0.60)) {
                $targetHeight = [int][Math]::Round($Size * 0.60)
                $targetWidth = [int][Math]::Round($targetHeight * $Foreground.Width / $Foreground.Height)
            }
            $left = [int][Math]::Round(($Size - $targetWidth) / 2)
            $top = [int][Math]::Round(($Size - $targetHeight) / 2)
            $graphics.DrawImage($Foreground, $left, $top, $targetWidth, $targetHeight)
        } finally {
            $graphics.Dispose()
        }
        $directory = Split-Path -Parent $Path
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
        $canvas.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $canvas.Dispose()
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
        foreach ($bytes in $images) { $writer.Write($bytes) }
    } finally {
        $writer.Dispose()
        $file.Dispose()
    }
}

$master = [System.Drawing.Bitmap]::FromFile($masterPath)
try {
    $androidSizes = @{
        'mipmap-mdpi' = 48
        'mipmap-hdpi' = 72
        'mipmap-xhdpi' = 96
        'mipmap-xxhdpi' = 144
        'mipmap-xxxhdpi' = 192
    }
    $adaptiveSizes = @{
        'mipmap-mdpi' = 108
        'mipmap-hdpi' = 162
        'mipmap-xhdpi' = 216
        'mipmap-xxhdpi' = 324
        'mipmap-xxxhdpi' = 432
    }
    $androidRoot = Join-Path $appRoot 'android/app/src/main/res'
    $foreground = New-ForegroundImage $master
    try {
        foreach ($entry in $androidSizes.GetEnumerator()) {
            Save-ResizedPng $master $entry.Value (Join-Path $androidRoot "$($entry.Key)/ic_launcher.png")
        }
        foreach ($entry in $adaptiveSizes.GetEnumerator()) {
            Save-AdaptiveForeground $foreground $entry.Value (Join-Path $androidRoot "$($entry.Key)/ic_launcher_foreground.png")
        }
    } finally {
        $foreground.Dispose()
    }

    $iosRoot = Join-Path $appRoot 'ios/Runner/Assets.xcassets/AppIcon.appiconset'
    $iosSizes = @{
        'Icon-App-20x20@1x.png' = 20; 'Icon-App-20x20@2x.png' = 40; 'Icon-App-20x20@3x.png' = 60
        'Icon-App-29x29@1x.png' = 29; 'Icon-App-29x29@2x.png' = 58; 'Icon-App-29x29@3x.png' = 87
        'Icon-App-40x40@1x.png' = 40; 'Icon-App-40x40@2x.png' = 80; 'Icon-App-40x40@3x.png' = 120
        'Icon-App-60x60@2x.png' = 120; 'Icon-App-60x60@3x.png' = 180
        'Icon-App-76x76@1x.png' = 76; 'Icon-App-76x76@2x.png' = 152
        'Icon-App-83.5x83.5@2x.png' = 167; 'Icon-App-1024x1024@1x.png' = 1024
    }
    foreach ($entry in $iosSizes.GetEnumerator()) {
        Save-ResizedPng $master $entry.Value (Join-Path $iosRoot $entry.Key)
    }

    $macRoot = Join-Path $appRoot 'macos/Runner/Assets.xcassets/AppIcon.appiconset'
    foreach ($size in @(16, 32, 64, 128, 256, 512, 1024)) {
        Save-ResizedPng $master $size (Join-Path $macRoot "app_icon_$size.png")
    }

    $windowsIcon = Join-Path $appRoot 'windows/runner/resources/app_icon.ico'
    Write-PngIco $master @(16, 24, 32, 48, 64, 128, 256) $windowsIcon
} finally {
    $master.Dispose()
}

Write-Host "Generated Nightfire icons from $masterPath"
