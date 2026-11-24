# Audio box

A mesh node that plays a show's soundtrack and broadcasts the show position
(SHOW_TIME), so armed field modules fire their cues in time with the music.
The track carries SMPTE LTC timecode on its right channel; the box decodes
its own output, so the position it sends is what the speakers are playing.

The design, protocol and build order are in [design.md](design.md).

## Status

- Step 1 (field modules follow SHOW_TIME) and step 2 (bridge and app can
  start and run a show that follows the box) are done.
- Step 3: the box firmware runs on a fake clock (audio-box 0.1.0). It runs
  on the old LCD dummy (COM18) and passed the bench check with the bridge
  (`scripts/bench/test_audio_box_bench.py`, 19 of 19). Still to do: modules
  following it on the bench, and the radio A/B.
- Step 4 (LTC capture and decoding from a real player) is not started.

## Layout

| Path | Contents |
|---|---|
| `design.md` | Design: protocol, module and app behaviour, hardware, build order |
| `main/audio_main.py` | Box firmware (fake clock until step 4) |
| `main/audio_boot.py` | Installed as `main.py`: loads `audio_main` |
| `configs/audio_box.py` | Default config, installed as `nrf_node_config.py` |
| `sim/sim_audio_node.py` | Scripted stand-in box for the simulator (node kind `audio`) |

Code the box shares with the other devices (`nfcp.py`, `mesh.py`,
`module_link.py`, `module_ota.py`, `nrf_module_common.py`) stays in
`../main/`. The tests are `../scripts/tests/test_audio_box.py`; the
simulator runs the real firmware as node kind `audiobox`.

## Installing

The board needs the Nightfire MicroPython image (`firmware/nrf52840/`).
Then, from the repository root (or the VS Code task "Audio box: upload"):

    powershell -ExecutionPolicy Bypass -File scripts\install\upload_audio_box.ps1 -Port COM7 -ConfigPath audio_box\configs\audio_box.py

To pair it with the bridge like a module, set `PAIR_BUTTON_PIN` in the
config to a pin with a button to ground and hold the button at power-up.

## Tests

From the repository root:

    py -3 scripts/tests/test_audio_box.py
    py -3 scripts/sim/mesh_sim.py --scenario audio_box
    py -3 scripts/sim/mesh_sim.py --scenario audio_chase
