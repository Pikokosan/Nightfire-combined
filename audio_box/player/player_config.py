# Audio box player pins (ESP32 DevKit, stock MicroPython). Installed as
# player_config.py next to player_main.py. See audio_box/README.md for wiring.

# PCM5102A DAC (I2S). Tie the DAC's SCK to GND (it makes its own clock),
# FMT to GND (I2S), FLT and DEMP to GND.
I2S_BCK_PIN = 26
I2S_WS_PIN = 25           # LRCK / LCK
I2S_DIN_PIN = 22
DAC_XSMT_PIN = 21         # soft mute (low = muted); None if XSMT is tied high

# SD card over SPI (machine.SDCard slot 2).
SD_SLOT = 2
SD_SCK_PIN = 18
SD_MOSI_PIN = 23
SD_MISO_PIN = 19
SD_CS_PIN = 5

# UART to the audio box's nRF.
UART_ID = 2
UART_BAUD = 115200
UART_TX_PIN = 17          # to the nRF's RX
UART_RX_PIN = 16          # from the nRF's TX
