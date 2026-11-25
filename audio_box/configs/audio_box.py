# Audio box config, installed as nrf_node_config.py (audio_box/README.md).
UNIQUE_ID = 0x00000000          # 0: use the chip's own id
GROUP_ID = 0x0001
NETWORK_ID = 0x04D2
RADIO_CHANNEL = 90
PAIR_BUTTON_PIN = None          # a pin number: hold it low at power-up to pair

# The player: "fake" (the box's own clock, no audio) or "uart" (the ESP32
# player, audio_box/player/, on a UART).
PLAYER = "fake"
FAKE_TRACK_MS = 600000          # "fake": every track is a 10 min fake track
PLAYER_UART_ID = 0              # "uart": nice!nano UART and pins
PLAYER_UART_BAUD = 115200
PLAYER_UART_TX_PIN = 6          # P0.06 to the ESP32's RX (GPIO16)
PLAYER_UART_RX_PIN = 8          # P0.08 from the ESP32's TX (GPIO17)
