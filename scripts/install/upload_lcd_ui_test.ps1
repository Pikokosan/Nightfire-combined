param(
    [string]$Port = "COM10",
    [int]$Baud = 115200,
    [string]$ConfigPath = ".\main\configs\lcd_ui_test.py"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$PreferredPython = "C:\Users\hog28\.micropython-studio\.venv\Scripts\python.exe"
$Python = "python"
$Uploader = Join-Path $Root "scripts\lib\raw_micropython_upload.py"

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
    "--delete", "lcd1602_i2c.py",
    "--delete", "nrf_node_config.py",
    "--delete", "nfcp.py",
    "--delete", "module_ota.py",
    "--delete", "field_measurement.py",
    "--delete", "adc_scaling.py",
    "--delete", "analog_mux74hc4051.py",
    "--delete", "shift_register74hc595.py"
)

$UploadArgs += @(
    ".\main\lcd_button_ui_test.py:main.py",
    ".\main\lcd1602_i2c.py:lcd1602_i2c.py"
)

if ($ConfigPath) {
    $UploadArgs += @("$($ConfigPath):nrf_node_config.py")
}

Push-Location $Root
try {
    & $Python @UploadArgs

    if ($LASTEXITCODE -ne 0) {
        throw "LCD UI test upload failed with exit code $LASTEXITCODE."
    }

    Write-Host "Done. lcd_button_ui_test.py is now installed as main.py on $Port."
}
finally {
    Pop-Location
}
