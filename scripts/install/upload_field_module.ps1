param(
    [string]$Port = "COM10",
    [int]$Baud = 115200,
    [ValidateSet("existing", "promicro", "ms88sfa")]
    [string]$Pinout = "existing",
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$PreferredPython = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Python = "python"
$Uploader = Join-Path $Root "scripts\lib\raw_micropython_upload.py"
$BundleBuilder = Join-Path $Root "scripts\build\build_field_mpy_bundle.py"
$BuildDir = Join-Path $Root "build\field_module_upload"

if ($ConfigPath -and $Pinout -ne "existing") {
    throw "Use either -ConfigPath or -Pinout, not both."
}

if (-not $ConfigPath) {
    $ConfigPath = switch ($Pinout) {
        "promicro" { ".\main\configs\field_promicro.py" }
        "ms88sfa" { ".\main\configs\field_ms88sfa.py" }
        default { "" }
    }
}

if (Test-Path -LiteralPath $PreferredPython) {
    try {
        & $PreferredPython --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $Python = $PreferredPython
        }
    }
    catch {
        $Python = "python"
    }
}

function Convert-ToWslPath {
    param([string]$Path)

    $FullPath = [IO.Path]::GetFullPath($Path)
    $Drive = $FullPath.Substring(0, 1).ToLowerInvariant()
    return "/mnt/$Drive" + $FullPath.Substring(2).Replace("\", "/")
}

function Compile-Mpy {
    param(
        [string]$Source,
        [string]$Output
    )

    $NativeCompiler = Get-Command "mpy-cross" -ErrorAction SilentlyContinue
    if ($NativeCompiler) {
        try {
            & $NativeCompiler.Source -O3 -o $Output $Source
            if ($LASTEXITCODE -eq 0) {
                return
            }
        }
        catch {
            Write-Host "Native mpy-cross could not start; trying WSL."
        }
    }

    $SourceWsl = Convert-ToWslPath $Source
    $OutputWsl = Convert-ToWslPath $Output
    & wsl.exe -- sh -lc 'compiler=$(command -v mpy-cross || printf "%s" "$HOME/.local/bin/mpy-cross"); exec "$compiler" "$@"' sh -O3 -o $OutputWsl $SourceWsl
    if ($LASTEXITCODE -ne 0) {
        throw "mpy-cross failed. Install mpy-cross 1.29 in Windows or WSL."
    }
}

function Wait-SerialPort {
    param(
        [string]$PortName,
        [int]$TimeoutSeconds = 15
    )

    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ([IO.Ports.SerialPort]::GetPortNames() -contains $PortName) {
            Start-Sleep -Milliseconds 500
            return
        }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $Deadline)

    throw "$PortName did not return after the cleanup reset."
}

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
$BundleSource = Join-Path $BuildDir "field_main_bundle.py"
$BundleMpy = Join-Path $BuildDir "field_main.mpy"
$ConfigMpy = Join-Path $BuildDir "nrf_node_config.mpy"

& $Python $BundleBuilder --main-dir (Join-Path $Root "main") --output $BundleSource
if ($LASTEXITCODE -ne 0) {
    throw "Failed to build the compact field-module source bundle."
}

Compile-Mpy $BundleSource $BundleMpy

if ($ConfigPath) {
    Compile-Mpy $ConfigPath $ConfigMpy
}

$CleanupArgs = @(
    $Uploader,
    "--port", $Port,
    "--baud", "$Baud",
    "--delete", "main.py",
    "--delete", "main.mpy",
    "--delete", "field_main.py",
    "--delete", "field_main.mpy",
    "--delete", "nfcp.py",
    "--delete", "nfcp.mpy",
    "--delete", "module_ota.py",
    "--delete", "module_ota.mpy",
    "--delete", "nrf_module_common.py",
    "--delete", "nrf_module_common.mpy",
    "--delete", "module_link.py",
    "--delete", "module_link.mpy",
    "--delete", "module_script.py",
    "--delete", "module_script.mpy",
    "--delete", "module_lcd.py",
    "--delete", "module_lcd.mpy",
    "--delete", "shift_register74hc595.py",
    "--delete", "shift_register74hc595.mpy",
    "--delete", "analog_mux74hc4051.py",
    "--delete", "analog_mux74hc4051.mpy",
    "--delete", "adc_scaling.py",
    "--delete", "adc_scaling.mpy",
    "--delete", "field_measurement.py",
    "--delete", "field_measurement.mpy",
    "--delete", "lcd1602_i2c.py",
    "--delete", "lcd1602_i2c.mpy",
    "--delete", "nrf_dummy_main.py",
    "--delete", "nrf_dummy_main.mpy",
    "--delete", "bridge_radio.py",
    "--delete", "field_hardware_test.py"
)

if ($ConfigPath) {
    $CleanupArgs += @(
        "--delete", "nrf_node_config.py",
        "--delete", "nrf_node_config.mpy"
    )
}

$UploadArgs = @(
    $Uploader,
    "--port", $Port,
    "--baud", "$Baud",
    ".\main\field_boot.py:main.py",
    "$($BundleMpy):field_main.mpy"
)

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigMpy):nrf_node_config.mpy")
}

Push-Location $Root
try {
    Write-Host "Removing the previous field image and reclaiming flash..."
    & $Python @CleanupArgs
    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 field module cleanup failed with exit code $LASTEXITCODE."
    }

    Wait-SerialPort $Port
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 field module upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. compact field firmware is installed on $Port (pinout: $Pinout)."
}
finally {
    Pop-Location
}
