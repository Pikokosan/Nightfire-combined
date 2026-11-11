# Field module LCD and buttons

The field module can drive an optional 2x16 I2C LCD. If the display is not
present, the firmware logs that and continues normally.

Default MS88SFA LCD and button mapping:

- LCD SDA: MicroPython pin `26` / nRF `P0.26`
- LCD SCL: MicroPython pin `27` / nRF `P0.27`
- Up / pair button: MicroPython pin `45` / nRF `P1.13`
- Down button: MicroPython pin `47` / nRF `P1.15`
- OK / forward button: MicroPython pin `15` / nRF `P0.15`
- Back button: MicroPython pin `20` / nRF `P0.20`

Production field-module buttons pass through an inverting 74HC14. The field
firmware therefore treats the nRF-side signal as active high and does not
enable an internal GPIO pull-up. Keep the external pull resistor on the
74HC14 input side and power the 74HC14 from 3.3 V.

For the complete Pro Micro field-module mapping, including the 74HC595,
pre-test, ADC, LEDs, and hardware ARM output, use
`main/configs/field_promicro.py` and the `-Pinout promicro` upload
option. The production mapping remains in
`main/configs/field_ms88sfa.py`.

UP and DOWN move between pages, BACK returns to the home page, and OK refreshes
continuity when viewing continuity pages. The LCD shows field-module system
status, UID/network/radio channel, battery/firmware information, script state,
and continuity summary/detail pages.

For LCD and button-only bench testing, upload the standalone UI test app:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install\upload_lcd_ui_test.ps1 -Port COM10
```

That app does not use the radio or field outputs. It simulates system,
network, battery, and continuity pages so the LCD/button flow can be tested
while a separate dummy module handles any master communication tests.

Standalone nRF52840 Pro Micro LCD test wiring:

- LCD SDA: board `D8` / nRF `P1.04` / MicroPython pin `36`
- LCD SCL: board `D9` / nRF `P1.06` / MicroPython pin `38`
- UP button: board `D15` / nRF `P1.13` / MicroPython pin `45`
- DOWN button: board `D18` / nRF `P1.15` / MicroPython pin `47`
- OK button: board `D14` / nRF `P1.11` / MicroPython pin `43`
- BACK button: board `D16` / nRF `P0.10` / MicroPython pin `10`

The standalone LCD test app reads buttons directly with internal pull-ups, so
wire each test button between its GPIO pin and `GND`. Use 3.3 V-safe I2C
wiring for the LCD.
