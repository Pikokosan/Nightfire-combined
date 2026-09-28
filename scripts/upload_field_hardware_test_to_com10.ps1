param(
    [string]$Port = "COM10",
    [int]$Baud = 115200,
    [string]$ConfigPath = ""
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PreferredPython = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Python = "python"
$Uploader = Join-Path $Root "scripts\raw_micropython_upload.py"

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

$UploadArgs = @(
    $Uploader,
    "--port", $Port,
    "--baud", "$Baud",
    "--delete", "main.py",
    "--delete", "nfcp.py",
    "--delete", "module_ota.py",
    "--delete", "shift_register74hc595.py",
    "--delete", "analog_mux74hc4051.py",
    "--delete", "adc_scaling.py",
    "--delete", "field_measurement.py",
    "--delete", "bridge_radio.py"
)

if ($ConfigPath) {
    $UploadArgs += @("--delete", "nrf_node_config.py")
}

$UploadArgs += @(
    ".\main\field_hardware_test.py:main.py",
    ".\main\shift_register74hc595.py:shift_register74hc595.py",
    ".\main\analog_mux74hc4051.py:analog_mux74hc4051.py",
    ".\main\adc_scaling.py:adc_scaling.py",
    ".\main\field_measurement.py:field_measurement.py"
)

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigPath):nrf_node_config.py")
}

Push-Location $Root
try {
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "nRF52840 field hardware test upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. field_hardware_test.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
