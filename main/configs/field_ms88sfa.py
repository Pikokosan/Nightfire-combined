# Proposed Minewsemi MS88SFA-nRF52840 field-module wiring.
#
# MicroPython pin numbering follows the local nRF52840 board convention:
# P0.nn -> nn, P1.nn -> 32 + nn.

BOARD_PINOUT = "ms88sfa"

# Control, status, buttons, and ADC.
PRE_TEST_PIN = 46           # Module pin 9,  P1.14
ARM_CONTROL_PIN = 13        # Module pin 30, P0.13
ARM_CONTROL_ACTIVE_VALUE = 1
SAFE_LED_PIN = 42           # Module pin 7,  P1.10
ARM_LED_PIN = 44            # Module pin 8,  P1.12
UP_BUTTON_PIN = 45          # Module pin 2,  P1.13
DOWN_BUTTON_PIN = 47        # Module pin 3,  P1.15
OK_FWD_BUTTON_PIN = 15      # Module pin 31, P0.15
OK_BUTTON_PIN = OK_FWD_BUTTON_PIN
BACK_BUTTON_PIN = 20        # Module pin 33, P0.20
BATT_CHECK_PIN = 29         # Module pin 5,  P0.29 / AIN5
BATTERY_ADC_ENABLED = True

# The no-4051 field-module design reads the selected cue through SHORT_CHECK.
SHORT_CHECK_PIN = 31        # Module pin 6,  P0.31 / AIN7
SENSE_SELECTED_ADC_PIN = SHORT_CHECK_PIN

# 2x16 I2C LCD.
LCD_SDA_PIN = 26            # Module pin 18, P0.26
LCD_SCL_PIN = 27            # Module pin 16, P0.27

# Three daisy-chained 74HC595 shift registers.
HC595_DATA_PIN = 6          # Module pin 20, P0.06
HC595_CLOCK_PIN = 8         # Module pin 21, P0.08
HC595_LATCH_PIN = 11        # Module pin 24, P0.11
HC595_Q7S_PIN = 22          # Module pin 34, P0.22, serial feedback from final 595
HC595_OE_PIN = 24           # Module pin 35, P0.24, active-low
HC595_SRCLR_PIN = 32        # Module pin 36, P1.00, active-low
HC595_CLOCK_DELAY_US = 1
STARTUP_595_CHAIN_TEST_ENABLED = True
HC595_EXPECTED_CHAIN_BITS = 24
HC595_CHAIN_TEST_MAX_BITS = 64

# The field module no longer uses 74HC4051 muxes or an external multi-channel
# ADC. Continuity/resistance testing asserts PRE_TEST, selects exactly one cue
# through the 74HC595 bank, reads SHORT_CHECK, then clears everything.
MEASUREMENT_METHOD = "pretest_selected_cue_short_check"
PRETEST_SETTLE_MS = 5
PRETEST_OUTPUT_SETTLE_US = 750
PRETEST_ADC_SAMPLES = 16
PRETEST_ADC_SAMPLE_DELAY_US = 150
# The low-current pre-test path is active-low at SHORT_CHECK: connected cues
# pull the ADC low, while open cues remain high.
CHANNEL_OPEN_MIN_FRACTION = 0.15

# All outputs from the three 74HC595 devices are field-module cues.
CUE_COUNT = 24

# Legacy direct channel-sense divider value. The MS88SFA no-4051 schematic uses
# SHORT_CHECK through the pre-test path instead; keep this only for shared code
# paths until the resistance equation is calibrated for the new circuit.
CHANNEL_SENSE_DIVIDER_RATIO = 22.0 / 122.0

# MS88SFA has no SuperMini-style P0.13 board VCC cutoff behavior.
EXTERNAL_VCC_CONTROL_ENABLED = False

# Optional spare if a future board adds a test-current gate or mux power gate.
AUX_TEST_EN_PIN = 5         # Module pin 17, P0.05 / AIN3
