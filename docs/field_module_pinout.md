# Field Module Pinout

This document tracks the nRF52840 field-module pinouts used by the active
Nightfire firmware. The current firmware entrypoint is `main/field_main.py`.

## MS88SFA nRF52840 Field Module Pinout

This is the proposed production field-module mapping for the Minewsemi
MS88SFA-nRF52840 module. The module exposes 29 GPIOs and has separate `VDD`,
`VDDH`, `VBUS`, `VCC_PA`, USB, and SWD pins, so keep those power/debug nets
separate from the application GPIO list.

The local MicroPython board definition addresses pins as integers:
`P0.nn` is `nn`, and `P1.nn` is `32 + nn`. For example, `P1.13` is `45`
and `P1.15` is `47`.

### MS88SFA Pin Table From Datasheet

This table follows the MS88SFA pin description in the Minewsemi datasheet.
The physical pad-numbering pattern matches the MS88SFB package style previously
used as a reference: left side pins 1-14, bottom pins 15-26, right side pins
27-41. Do not reuse the MS88SFB signal labels on the right side; several
signals differ even though the pad locations and numbering pattern match.

| Pin | Module Net | Current Field-Module Use |
| --- | --- | --- |
| 1 | `GND` | Ground |
| 2 | `P1.13` | `UP_BTN` / `PAIR_BTN` |
| 3 | `P1.15` | `DWN_BTN` |
| 4 | `P0.02` / `AIN0` | Spare ADC-capable pin |
| 5 | `P0.29` / `AIN5` | `BATT_CHECK` |
| 6 | `P0.31` / `AIN7` | `SHORT_CHECK` |
| 7 | `P1.10` | `SAFE_LED` |
| 8 | `P1.12` | `ARM_LED` |
| 9 | `P1.14` | `PRE_TEST` |
| 10 | `P0.03` / `AIN1` | Spare ADC-capable pin |
| 11 | `P0.28` / `AIN4` | Spare ADC-capable pin |
| 12 | `P0.30` / `AIN6` | Spare ADC-capable pin |
| 13 | `GND` | Ground |
| 14 | `VDD` | 3.3 V module logic supply |
| 15 | `VCC_PA` | 3.3 V PA supply |
| 16 | `P0.27` | `LCD_SCL` |
| 17 | `P0.05` / `AIN3` | Spare / optional `AUX_TEST_EN` |
| 18 | `P0.26` | `LCD_SDA` |
| 19 | `P0.04` / `AIN2` | Spare ADC-capable pin |
| 20 | `P0.06` | `HC595_DATA` |
| 21 | `P0.08` | `HC595_CLOCK` |
| 22 | `P1.09` | Spare GPIO |
| 23 | `P0.12` | Spare GPIO |
| 24 | `P0.11` | `HC595_LATCH` |
| 25 | `VBUS` | USB VBUS |
| 26 | `GND` | Ground |
| 27 | `VDDH` | Tie to 3.3 V rail for this design |
| 28 | `D_N` | USB D- |
| 29 | `D_P` | USB D+ |
| 30 | `P0.13` | `ARM` hardware-control net |
| 31 | `P0.15` | `OK_FWD_BTN` |
| 32 | `P0.18` | Do not use; left unused for SFB/SFA footprint compatibility |
| 33 | `P0.20` | `BACK_BTN` |
| 34 | `P0.22` | `HC595_Q7S` |
| 35 | `P0.24` | `HC595_OE` |
| 36 | `P1.00` | `HC595_SRCLR` |
| 37 | `SWDIO` | Programmer/debug |
| 38 | `SWDCLK` | Programmer/debug |
| 39 | `P0.09` | Avoid unless NFC is disabled |
| 40 | `P0.10` | Avoid unless NFC is disabled |
| 41 | `GND` | Ground |

### Programming And Power

| Signal | Module Pin | nRF Pin | Use |
| --- | --- | --- | --- |
| `VDD` | 14 | `VDD` | 1.7-3.6 V logic supply; use 3.3 V for this field module |
| `VDDH` | 27 | `VDDH` | Tie to 3.3 V when the module is powered from the 3.3 V rail |
| `VCC_PA` | 15 | `VCC_PA` | Stable 2.7-3.6 V PA supply; tie to the same 3.3 V rail unless RF testing says otherwise |
| `GND` | 1, 13, 26, 41 | `GND` | Ground |
| `SWDIO` | 37 | `SWDIO` | Programmer/debug only |
| `SWDCLK` | 38 | `SWDCLK` | Programmer/debug only |
| `VBUS` | 25 | `VBUS` | USB VBUS sense/input; reserve for USB |
| `USB_D-` | 28 | `D_N` | USB data minus; reserve for USB |
| `USB_D+` | 29 | `D_P` | USB data plus; reserve for USB |

### Control, Status, Buttons, And ADC

| Signal | Module Pin | nRF Pin | MicroPython Pin | Use |
| --- | --- | --- | --- | --- |
| `PRE_TEST` | 9 | `P1.14` | `46` | Pre-arm / pre-test control |
| `SAFE_LED` | 7 | `P1.10` | `42` | Safe / unarmed green LED |
| `ARM_LED` | 8 | `P1.12` | `44` | Armed red LED |
| `UP_BTN` / `PAIR_BTN` | 2 | `P1.13` | `45` | Up button; hold for pairing mode |
| `DWN_BTN` | 3 | `P1.15` | `47` | Down button |
| `OK_FWD_BTN` | 31 | `P0.15` | `15` | OK / forward button |
| `BACK_BTN` | 33 | `P0.20` | `20` | Back button |
| `BATT_CHECK` | 5 | `P0.29` / `AIN5` | `29` | Analog battery ADC sense |
| `SHORT_CHECK` | 6 | `P0.31` / `AIN7` | `31` | Analog cue-test ADC sense |
| `ARM` | 30 | `P0.13` | `13` | Hardware arm/pre-test circuit control |

Use `P0.13` for the `ARM` hardware-control net. This preserves ADC-capable
spares for future measurement/debug and avoids the NFC-adjacent pins.

### LCD I2C

| Signal | Module Pin | nRF Pin | MicroPython Pin | Use |
| --- | --- | --- | --- | --- |
| `LCD_SDA` | 18 | `P0.26` | `26` | I2C SDA for 2x16 LCD |
| `LCD_SCL` | 16 | `P0.27` | `27` | I2C SCL for 2x16 LCD |

Use external pull-ups on the LCD board or add board-level pull-ups to 3.3 V if
the display does not include them.

### 18-Channel Prototype Output Interface

| Signal | Module Pin | nRF Pin | MicroPython Pin | Use |
| --- | --- | --- | --- | --- |
| `HC595_DATA` | 20 | `P0.06` | `6` | 74HC595 SER/DATA |
| `HC595_CLOCK` | 21 | `P0.08` | `8` | 74HC595 SRCLK |
| `HC595_LATCH` | 24 | `P0.11` | `11` | 74HC595 RCLK/latch |
| `HC595_Q7S` | 34 | `P0.22` | `22` | Serial feedback from Q7S on the last 74HC595 |
| `HC595_OE` | 35 | `P0.24` | `24` | 74HC595 active-low output enable; hardware pull-up required |
| `HC595_SRCLR` | 36 | `P1.00` | `32` | 74HC595 active-low clear |

Firmware shifts and latches all zeros before pulling `HC595_OE` low. Outputs
1-18 are used from the 24 available 74HC595 outputs.

When requested, firmware can run a 74HC595 chain-detect diagnostic by clocking a
single marker bit through the daisy chain and reading it back on `HC595_Q7S`.
This diagnostic keeps `/OE` disabled and leaves the output bank latched low. It
detects the number of shift-register output positions present in the chain; cue
load presence is still measured separately with `PRE_TEST` and `SHORT_CHECK`.

For the MS88SFA field module, this diagnostic is also a startup self-test. The
expected chain length is 24 bits. If the marker bit is not observed on
`HC595_Q7S`, or if the returned bit count is not 24, firmware latches a startup
fault and rejects arm, fire, script start, and continuity/pre-test requests.
Firmware requests an MCU pull-down on `HC595_Q7S` so a missing feedback wire
fails low instead of floating high.

### Cue Measurement Method

The MS88SFA field-module revision does not use 74HC4051 analog muxes or an
external multi-channel ADC. Cue measurement uses the low-current pre-test path:

1. Clear the 74HC595 output bank and keep `/OE` disabled.
2. Assert `PRE_TEST`.
3. Shift/latch exactly one selected cue output high.
4. Enable `/OE` only long enough to read `SHORT_CHECK`.
5. Average the `SHORT_CHECK` ADC samples.
6. Disable `/OE`, clear/latch all outputs low, then deassert `PRE_TEST`.

`SHORT_CHECK` is the single ADC observation point for all cues. The selected
cue is determined by the 74HC595 output bit that is temporarily enabled during
the low-current test window. The resistance equation and classification
thresholds must be calibrated from the actual `PRE_TEST` current path; do not
reuse the older raw-channel divider equation for this schematic.

The output-stage sheet currently shows 24 cue MOSFET outputs from three
74HC595 devices. Firmware can keep `CUE_COUNT = 18` for the prototype or raise
it to 24 after the rest of the system is ready for the larger output count.

### Optional Spare GPIO

| Suggested Use | Module Pin | nRF Pin | MicroPython Pin | Notes |
| --- | --- | --- | --- | --- |
| `AUX_TEST_EN` / spare control | 17 | `P0.05` / `AIN3` | `5` | Available spare control if the pre-test circuit needs another gate later |
| Spare ADC | 4 | `P0.02` / `AIN0` | `2` | Keep free for analog expansion |
| Spare ADC | 10 | `P0.03` / `AIN1` | `3` | Keep free for analog expansion |
| Spare ADC | 19 | `P0.04` / `AIN2` | `4` | Keep free for analog expansion |
| Spare ADC | 11 | `P0.28` / `AIN4` | `28` | Keep free for analog expansion |
| Spare ADC | 12 | `P0.30` / `AIN6` | `30` | Keep free for analog expansion |
| Spare GPIO | 22 | `P1.09` | `41` | General spare |
| Spare GPIO | 23 | `P0.12` | `12` | General spare |

Avoid assigning `P0.09` and `P0.10` unless NFC is intentionally disabled in the
nRF configuration. They can be GPIO, but this mapping does not need them.

## SuperMini nRF52840 Field Module Pinout

This mapping targets the SuperMini NRF52840 board shown in the current bench
pinout reference. It assumes the nRF52840 is the module MCU and radio.

The local MicroPython board definition addresses pins as integers:
`P0.nn` is `nn`, and `P1.nn` is `32 + nn`. For example, `P1.13` is `45`
and `P1.15` is `47`.

`P0.13` intentionally controls the board external 3.3 V/VCC cut-off path. The
prototype powers the 74HC4051 muxes from the board `VCC` pin, so firmware drives
`P0.13` high before scanning and waits for the rail to settle. Pulling `P0.13`
low turns that external VCC rail off on this board.

### Control, Status, And Inputs

| Signal | Board Pin | MicroPython Pin | Use |
| --- | --- | --- | --- |
| `PRE_TEST` | `P0.09` / `D10` | `9` | Pre-arm / pre-test control |
| `SAFE_LED` | `P0.10` / `D16` | `10` | Safe / unarmed green LED |
| `ARM_LED` | `P0.11` / `D7` | `11` | Armed red LED |
| `UP_BTN` / `PAIR_BTN` | `P1.01` bottom pad | `33` | Up button; hold for pairing mode |
| `DWN_BTN` | `P1.02` bottom pad | `34` | Down button |
| `BATT_CHECK` | `P0.29` / `D20` / `AIN5` | `29` | Analog battery ADC sense |
| `EXT_VCC_CTL` | `P0.13` | `13` | Board VCC control for 74HC4051 power; high = on |

### LCD I2C

| Signal | Board Pin | MicroPython Pin | Use |
| --- | --- | --- | --- |
| `LCD_SDA` | `P1.07` bottom pad | `39` | I2C SDA for future 2x16 LCD |
| `LCD_SCL` | Reassign later | `35` default placeholder | I2C SCL for future 2x16 LCD |

Use external pull-ups on the LCD board or add board-level pull-ups if the
display does not include them.

### Optional Service UART

The standalone hardware test uses the USB serial REPL for diagnostics. `P0.06`
and `P0.08` are assigned to the 74HC595 by default, so they are not available
for an external service UART in this prototype wiring.

If a separate UART is needed, choose alternate unused exposed pins and override
the firmware constants in `nrf_node_config.py`.

### 18-Channel Prototype Output Interface

| Signal | Board Pin | MicroPython Pin | Use |
| --- | --- | --- | --- |
| `HC595_DATA` | `P0.06` / `D1` | `6` | 74HC595 SER/DATA |
| `HC595_CLOCK` | `P0.08` / `D0` | `8` | 74HC595 SRCLK |
| `HC595_LATCH` | `P0.17` / `D2` | `17` | 74HC595 RCLK/latch |
| `HC595_OE` | `P0.20` / `D3` | `20` | 74HC595 active-low output enable; default disabled |
| `HC595_SRCLR` | `P0.22` / `D4` | `22` | 74HC595 active-low clear |

Firmware shifts and latches all zeros before pulling `HC595_OE` low. Outputs
1-18 are used from the 24 available 74HC595 outputs.

### 18-Channel Analog Scanner

| Signal | Board Pin | MicroPython Pin | Use |
| --- | --- | --- | --- |
| `MUX_S0` | `P0.24` / `D5` | `24` | Shared 74HC4051 address bit 0 |
| `MUX_S1` | `P1.00` / `D6` | `32` | Shared 74HC4051 address bit 1 |
| `MUX_S2` | `P1.04` / `D8` | `36` | Shared 74HC4051 address bit 2 |
| `MUX1_EN` | `P1.06` / `D9` | `38` | Active-low enable for channels 1-8 |
| `MUX2_EN` | `P1.15` / `D18` | `47` | Active-low enable for channels 9-16 |
| `MUX3_EN` | `P1.13` / `D15` | `45` | Active-low enable for channels 17-18 |
| `SENSE_SELECTED` | `P0.31` / `D21` / `AIN7` | `31` | Shared mux output into MCU ADC |

Only one 74HC4051 is enabled at a time. Firmware waits after channel switching
before averaging ADC samples.

### Programming And Power

| Signal | nRF Pin | Use |
| --- | --- | --- |
| `SWCLK` | SWD `CLK` pad | Programmer only |
| `SWDIO` | SWD `DIO` pad | Programmer only |
| `RESET` | Reset pad | Board reset / bootloader entry |
| `VCC` | `VCC` / `3V3` | Module supply |
| `GND` | `GND` | Ground |

### Spare nRF IO After This Mapping

These pins are not assigned by the proposed field-module pinout:

| nRF Pin | Notes |
| --- | --- |
| `P0.02` / `D19` / `AIN0` | Spare ADC-capable pin if LCD is not fitted |
| `P1.11` / `D14` | General spare GPIO |
| `P0.13` | Assigned to `EXT_VCC_CTL` for board VCC control |

Before final PCB routing, confirm continuity from the exact SuperMini board
variant because clone boards can move bottom-pad signals.

## Firmware Note

The nRF field-module firmware entrypoint is `main/field_main.py`. It uses the
18-channel output and measurement pinout from the active `nrf_node_config.py`.
Use `main/nrf_node_config_current_bench.py` for the SuperMini bench fixture and
`main/nrf_node_config_ms88sfa_field.py` for the proposed MS88SFA field-module
mapping. The SuperMini bench fixture still uses 74HC4051 muxes; the proposed
MS88SFA field-module mapping does not. All continuity development/testing is
treated as inert dummy-load testing. The current bench channel divider is `100k`
high-side and `22k` low-side, but the MS88SFA schematic no longer measures each
channel through that raw-sense divider. The MS88SFA measurement is a controlled
low-current pre-test sequence: enable `PRE_TEST`, select exactly one cue through
the 74HC595 output bank, read `SHORT_CHECK`, then immediately clear the output
bank and disable `PRE_TEST`. The resistance calculation should be added only
after the exact pre-test current path is measured and calibrated.
