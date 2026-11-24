# Audio box config, installed as nrf_node_config.py (audio_box/README.md).
UNIQUE_ID = 0x00000000          # 0: use the chip's own id
GROUP_ID = 0x0001
NETWORK_ID = 0x04D2
RADIO_CHANNEL = 90
FAKE_TRACK_MS = 600000          # step 3: every track is a 10 min fake track
PAIR_BUTTON_PIN = None          # a pin number: hold it low at power-up to pair
