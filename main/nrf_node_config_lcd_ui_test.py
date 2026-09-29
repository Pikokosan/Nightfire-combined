# LCD/button UI test wiring for an nRF52840 Pro Micro style board.
#
# MicroPython pin numbering follows the local nRF52840 board convention:
# P0.nn -> nn, P1.nn -> 32 + nn.
#
# Adjust these pin numbers to match the board wiring before upload if needed.

LCD_I2C_ID = 0
LCD_SDA_PIN = 36          # Board D8  / nRF P1.04
LCD_SCL_PIN = 38          # Board D9  / nRF P1.06
LCD_I2C_FREQ = 100000
LCD_ADDRESS = 0x3F
LCD_MAPPING = "upper"    # Common PCF8574 backpack map: D4-D7 on P4-P7.
LCD_COLS = 16
LCD_ROWS = 2

UP_BUTTON_PIN = 45        # Board D15 / nRF P1.13
DOWN_BUTTON_PIN = 47      # Board D18 / nRF P1.15
OK_FWD_BUTTON_PIN = 43    # Board D14 / nRF P1.11
OK_BUTTON_PIN = OK_FWD_BUTTON_PIN
BACK_BUTTON_PIN = 10      # Board D16 / nRF P0.10
BUTTON_DEBOUNCE_MS = 80

GROUP_ID = 1
NETWORK_ID = 0x04D2
RADIO_CHANNEL = 90
CUE_COUNT = 18
BATTERY_PACK_TYPE = "2S"
SIM_BATTERY_MV = 8200
CONTINUITY_PAGE_SIZE = 8
MASTER_LINK_TIMEOUT_MS = 15000
