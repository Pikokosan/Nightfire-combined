# Audio box

A mesh node that plays a show's soundtrack and broadcasts the show position
(SHOW_TIME), so armed field modules fire their cues in time with the music.
The track carries SMPTE LTC timecode on its right channel; the box decodes
its own output, so the position it sends is what the speakers are playing.

The design, protocol and build order are in [design.md](design.md).
Where the work stands, the bench, and the parts to order before going on:
[STATUS.md](STATUS.md).

## Status

- Step 1 (field modules follow SHOW_TIME) and step 2 (bridge and app can
  start and run a show that follows the box) are done.
- Step 3: the box firmware runs on a fake clock (audio-box 0.1.0). It runs
  on the old LCD dummy (COM18) and passed the bench check with the bridge
  (`scripts/bench/test_audio_box_bench.py`, 19 of 19). Still to do: modules
  following it on the bench, and the radio A/B.
- Step 4 (a real player): the player is an ESP32 with a PCM5102A DAC. The
  LTC reference encoder, decoder and test-track generator, the player
  firmware and the box's player driver (audio-box 0.2.0, `PLAYER = "uart"`)
  are done, tested on the PC only. The nRF's LTC capture is next.

## Layout

| Path | Contents |
|---|---|
| `design.md` | Design: protocol, module and app behaviour, hardware, build order |
| `STATUS.md` | Where we are, the bench boards, parts to order |
| `main/audio_main.py` | Box firmware (fake clock until step 4) |
| `main/audio_boot.py` | Installed as `main.py`: loads `audio_main` |
| `configs/audio_box.py` | Default config, installed as `nrf_node_config.py` |
| `main/player_link.py` | The box's driver for the ESP32 player (UART) |
| `main/ltc.py` | LTC encoder and decoder (reference for the nRF's native decoder) |
| `tools/make_ltc_track.py` | Makes test tracks: LTC on the right, clicks on the left |
| `player/player_main.py` | ESP32 player firmware: WAV from SD card to the PCM5102A, UART commands |
| `player/player_config.py` | Player pins, installed as `player_config.py` |
| `player/player_boot.py` | Installed on the ESP32 as `main.py` |

Code the box shares with the other devices (`nfcp.py`, `mesh.py`,
`module_link.py`, `module_ota.py`, `nrf_module_common.py`) stays in
`../main/`. The tests are `../scripts/tests/test_audio_box.py`.

## Installing

The board needs the Nightfire MicroPython image (`firmware/nrf52840/`).
Then, from the repository root (or the VS Code task "Audio box: upload"):

    powershell -ExecutionPolicy Bypass -File scripts\install\upload_audio_box.ps1 -Port COM7 -ConfigPath audio_box\configs\audio_box.py

To pair it with the bridge like a module, set `PAIR_BUTTON_PIN` in the
config to a pin with a button to ground and hold the button at power-up.

## The player (ESP32 + PCM5102A)

Wiring (ESP32 DevKit pins, `player/player_config.py`):

| ESP32 | To |
|---|---|
| GPIO26 | PCM5102A BCK |
| GPIO25 | PCM5102A LCK (LRCK) |
| GPIO22 | PCM5102A DIN |
| GPIO21 | PCM5102A XSMT (soft mute; or tie XSMT high and set `DAC_XSMT_PIN = None`) |
| GND | PCM5102A SCK, FMT, FLT, DEMP, GND |
| 3V3 | PCM5102A VIN, SD card VCC |
| GPIO18 / 23 / 19 / 5 | SD card SCK / MOSI / MISO / CS |
| GPIO17 (TX) / GPIO16 (RX) | the nice!nano's P0.08 (RX) / P0.06 (TX), and a common GND |

The PCM5102A's left output goes to the amplifier. The right output carries
the LTC back to the nRF (AC coupled and biased; that circuit comes with the
nRF capture).

On the box, set `PLAYER = "uart"` in its config (`configs/audio_box.py`)
and reinstall it. Install the player (stock MicroPython on the ESP32
first), then copy tracks to the SD card as `001.wav` ... `999.wav`:

    powershell -ExecutionPolicy Bypass -File scripts\install\upload_audio_player.ps1 -Port COM7

## Making a test track

    py -3 audio_box/tools/make_ltc_track.py 007.wav --seconds 60 --cues 500,1500,3000

## Tests

From the repository root:

    py -3 scripts/tests/test_audio_box.py
    py -3 scripts/tests/test_ltc.py
    py -3 scripts/tests/test_audio_player.py
    py -3 scripts/tests/test_player_link.py
