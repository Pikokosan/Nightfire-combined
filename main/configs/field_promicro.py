# nRF52840 Pro Micro / nice!nano field-module test wiring.
#
# MicroPython pin numbering used by the Nightfire build:
# P0.nn -> nn, P1.nn -> 32 + nn.
# This profile uses the same no-4051 measurement design as the MS88SFA field
# module so the application behavior remains identical between test boards.

BOARD_PINOUT = "promicro"

# Control, status, buttons, and ADC.
PRE_TEST_PIN = 2            # Board D19 / P0.02 / AIN0
ARM_CONTROL_PIN = 9         # Board D10 / P0.09, active high
ARM_CONTROL_ACTIVE_VALUE = 1
SAFE_LED_PIN = 11           # Board D7  / P0.11
ARM_LED_PIN = 24            # Board D5  / P0.24
UP_BUTTON_PIN = 45          # Board D15 / P1.13
DOWN_BUTTON_PIN = 47        # Board D18 / P1.15
OK_FWD_BUTTON_PIN = 43      # Board D14 / P1.11
OK_BUTTON_PIN = OK_FWD_BUTTON_PIN
BACK_BUTTON_PIN = 10        # Board D16 / P0.10
BATT_CHECK_PIN = 29         # Board D20 / P0.29 / AIN5
BATTERY_ADC_ENABLED = True
BATTERY_PACK_TYPE = "2S"
# Calibrated on the Pro Micro test fixture: raw 26840 = 8.09 V.
BATTERY_ADC_FULL_SCALE_MV = 19756

# The selected cue is measured through the common SHORT_CHECK ADC input.
SHORT_CHECK_PIN = 31        # Board D21 / P0.31 / AIN7
SENSE_SELECTED_ADC_PIN = SHORT_CHECK_PIN

# 2x16 I2C LCD.
LCD_SDA_PIN = 36            # Board D8 / P1.04
LCD_SCL_PIN = 38            # Board D9 / P1.06
LCD_I2C_ID = 0
LCD_I2C_FREQ = 100000

# Three daisy-chained 74HC595 shift registers.
HC595_DATA_PIN = 6          # Board D1 / P0.06
HC595_CLOCK_PIN = 8         # Board D0 / P0.08
HC595_LATCH_PIN = 17        # Board D2 / P0.17
HC595_OE_PIN = 20           # Board D3 / P0.20, active low
HC595_SRCLR_PIN = 22        # Board D4 / P0.22, active low
HC595_Q7S_PIN = 32          # Board D6 / P1.00, final 595 feedback
HC595_CLOCK_DELAY_US = 1
# Validate all three daisy-chained 74HC595 devices through the final Q7S output.
STARTUP_595_CHAIN_TEST_ENABLED = True
HC595_EXPECTED_CHAIN_BITS = 24
HC595_CHAIN_TEST_MAX_BITS = 64

# Match the production field-module continuity method.
MEASUREMENT_METHOD = "pretest_selected_cue_short_check"
PRETEST_SETTLE_MS = 5
PRETEST_OUTPUT_SETTLE_US = 750
PRETEST_ADC_SAMPLES = 16
PRETEST_ADC_SAMPLE_DELAY_US = 150
# A connected cue pulls SHORT_CHECK near 0 V; an open cue measures about
# 2.51 V. The largest calibrated load (22 ohms) measures about 1.23 V, while
# a true open measures about 2.51 V. Keep the threshold between those states.
CHANNEL_OPEN_MIN_FRACTION = 0.23

# Measured on the Pro Micro field fixture using known loads. Points are
# (SHORT_CHECK ADC millivolts, deci-ohms): 0.0, 2.0, 4.7, and 22 ohms.
RESISTANCE_CALIBRATION_POINTS = ((26, 0), (158, 20), (442, 47), (1228, 220))

CUE_COUNT = 24
CHANNEL_SENSE_DIVIDER_RATIO = 22.0 / 122.0

# The Pro Micro test board does not need the older SuperMini VCC switch path.
EXTERNAL_VCC_CONTROL_ENABLED = False
