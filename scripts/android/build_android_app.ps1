param(
    [string]$JdkPath = $env:JAVA_HOME,
    [switch]$Check,
    [ValidateSet('android-arm64', 'android-x64')]
    [string]$TargetPlatform = 'android-arm64'
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$Flutter = Join-Path $RepoRoot "tools/flutter/bin/flutter.bat"
$Sdk = Join-Path $RepoRoot "tools/android-sdk"
if (-not (Test-Path -LiteralPath $Flutter)) {
    throw "Workspace Flutter SDK missing. Install Flutter in tools/flutter first."
}
if (-not (Test-Path -LiteralPath $Sdk)) {
    throw "Workspace Android SDK missing. Install the SDK in tools/android-sdk first."
}
if (-not $JdkPath) {
    throw "Supply -JdkPath or set JAVA_HOME to a compatible JDK."
}
$env:JAVA_HOME = $JdkPath
$env:ANDROID_HOME = $Sdk
$env:ANDROID_SDK_ROOT = $Sdk
$env:PUB_CACHE = Join-Path $RepoRoot "tools/pub-cache"
$env:GRADLE_USER_HOME = Join-Path $RepoRoot "tools/gradle-cache"
$PreviousAppData = $env:APPDATA
$env:APPDATA = Join-Path $RepoRoot 'tools/flutter-profile'
$PreviousWindowsTarget = $env:FLUTTER_WINDOWS
# Android builds do not need desktop plugin symlinks or Windows Developer Mode.
$env:FLUTTER_WINDOWS = 'false'
Push-Location (Join-Path $RepoRoot "apps/nightfire_controller")
try {
    & $Flutter --suppress-analytics pub get --offline
    if ($LASTEXITCODE -ne 0) { throw 'Offline dependency resolution failed. Populate tools/pub-cache first.' }
    if ($Check) {
        & $Flutter --suppress-analytics analyze --no-pub
        if ($LASTEXITCODE -ne 0) { throw "Flutter analysis failed." }
        & $Flutter --suppress-analytics test --no-pub
        if ($LASTEXITCODE -ne 0) { throw "Flutter tests failed." }
    }
    & $Flutter --suppress-analytics build apk --debug --no-pub --target-platform $TargetPlatform
    if ($LASTEXITCODE -ne 0) { throw "Android APK build failed." }
}
finally {
    $env:FLUTTER_WINDOWS = $PreviousWindowsTarget
    $env:APPDATA = $PreviousAppData
    Pop-Location
}
