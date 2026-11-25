# Audio box: where we are, and parts to order

Written 2026-11-24. The design and the build order are in
[design.md](design.md); this page is the short version, the bench as it is
now, and what has to be bought before the work can go on.

## Where we are

| Step | What | State |
|---|---|---|
| 1 | Modules follow the box's SHOW_TIME (chase mode) | Done, committed (`2cac449`). Field 0.3.3+, dummy 0.2.3+ |
| 2 | Bridge and app start and run a chase show | Done, committed (`67aa272`). Bridge usb-master-0.4.7 on COM12. No app screen yet |
| 3 | Box firmware on a fake clock, bench-tested | Done, committed (`57948bc`). Box on COM18, one module (COM16) followed it in 10 shows within ~2 ms by its own clock |
| 4 | A real player and LTC | Half done, committed (`e2a71bd`, see below) |
| 5 | App screens: track picker, box status, audio delay, jump and loss counts | Not started |

**Step 4, done (tested on the PC only, committed in `e2a71bd`):**
- `main/ltc.py`: LTC encoder and decoder (the reference for the nRF's C
  decoder). Sync times within 0.3 µs on generated audio.
- `tools/make_ltc_track.py`: test tracks, LTC on the right, a click on the
  left at each cue.
- `player/`: ESP32 + PCM5102A player firmware (audio-player 0.1.0): WAV
  from an SD card, UART commands from the box.
- `main/player_link.py`: the box's driver for that player (audio-box
  0.2.0, `PLAYER = "uart"`), with fail-stop checks.
- Tests: `test_ltc.py`, `test_audio_player.py`, `test_player_link.py`;
  all 24 Python test files pass.

**Step 4, still to do:**
1. Run the player on a real ESP32 + PCM5102A + SD card (needs the parts
   below). Check: it plays, the UART commands work, the box drives it.
2. Native LTC capture in the nRF image (C, next to `nrf_radio`):
   comparator, PPI, TIMER capture, the decoder in C, tested against
   `ltc.py`. Needs a MicroPython image rebuild and a reflash of the box.
3. The LTC input circuit from the DAC's right output to the nRF (parts
   below).
4. Bench test 4: a generated track on the player, the click and a module
   output on two scope / logic-analyser channels.

**Other open items:**
- Bench test 5: radio A/B (`reliable_ping_load.py`) with the box sending
  SHOW_TIME.
- Bench test 3b with two modules at once: needs a second module (the old
  dummy is now the box).
- The app's "Arm all" would fail on the box: step 5 must leave device type
  0x30 out.
- Other field boards may have a dead 32 kHz crystal like COM16: they print
  `LFCLK on RC` at boot (field 0.3.5 calibrates it, +389 ppm on COM16).

## The bench now

| Port | Board | Runs |
|---|---|---|
| COM12 | USB bridge | usb-master-0.4.7 (backup of 0.4.6 in `dist/bridge_backup_20261124_131832`) |
| COM16 | Field module, Pro Micro nRF52840 clone, uid 0xD96E30DD | field-module 0.3.5; no working 32 kHz crystal; all 24 cues open (no igniters) |
| COM18 | Old LCD dummy, nice!nano, uid 0x62A75130 | audio-box 0.1.1 (fake clock). To make it a dummy again: `upload_nrf_dummy.ps1 -Port COM18 -ConfigPath main/configs/dummy_lcd.py` |

## Parts to order

For the player (step 4):

| Part | How many | Why / notes |
|---|---|---|
| ESP32 dev board (ESP32-WROOM-32 DevKit, 38-pin) | 1 (2 to have a spare) | Runs the player. The default pins in `player/player_config.py` assume this board; another ESP32 variant may need other pins |
| PCM5102A I2S DAC module | 1 (2 to have a spare) | The common modules have solder jumpers for SCK, FMT, FLT, DEMP and XSMT; set them as in the README wiring table |
| microSD card module, SPI, 3.3 V | 1 | For the tracks. A module made for 3.3 V logic (the ESP32 is 3.3 V) |
| microSD card, 8–32 GB | 1 | Formatted FAT32 |
| 3.5 mm stereo jack breakout | 1 | Left channel to the amplifier or a powered speaker to listen |

For the LTC input to the nRF (step 4):

| Part | How many | Why / notes |
|---|---|---|
| 1 µF capacitor (film or ceramic) | 2 | AC coupling from the DAC's right output |
| 100 kΩ resistors | 4 | Bias the signal to half the supply |
| 1 kΩ resistors | 2 | Series resistor into the nRF pin |
| Comparator, e.g. TLV3201 or LM393 (optional) | 1–2 | Only if the nRF's own comparator turns out too noisy |

For measuring and more modules:

| Part | How many | Why / notes |
|---|---|---|
| Logic analyser, 8 channels, 24 MHz (or a 2-channel scope) | 1 | End-to-end timing without USB in the path (bench test 4): the left-channel click against a module output. The USB-print method can't resolve 10 ms |
| nice!nano (nRF52840), genuine | 1–2 | A second module for bench test 3b (two modules at once), and a board with a working 32 kHz crystal |
| Breadboard, jumper wires, USB cables | a few | Wiring the player and the LTC input |
| 5 V supply (USB charger) | 1 | Powering the ESP32 and DAC away from the PC |

## When the parts arrive

1. Flash stock MicroPython on the ESP32, wire the DAC and SD card (README),
   copy a test track: `py -3 audio_box/tools/make_ltc_track.py 007.wav
   --seconds 60 --cues 500,1500,3000`, install the player with
   `scripts/install/upload_audio_player.ps1`.
2. Wire the ESP32's UART to COM18, set `PLAYER = "uart"` in the box config,
   reinstall the box (`upload_audio_box.ps1`), and drive it with
   `scripts/bench/test_audio_box_bench.py`.
3. Then the native LTC capture, and bench test 4 with the logic analyser.
