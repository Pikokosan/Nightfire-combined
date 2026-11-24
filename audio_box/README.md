# Audio box

A mesh node that plays a show's soundtrack and broadcasts the show position
(SHOW_TIME), so armed field modules fire their cues in time with the music.
The track carries SMPTE LTC timecode on its right channel; the box decodes
its own output, so the position it sends is what the speakers are playing.

The design, protocol and build order are in [design.md](design.md).

## Status

- Step 1 (field modules follow SHOW_TIME) and step 2 (bridge and app can
  start and run a show that follows the box) are done, in unit tests and the
  simulator only.
- There is no box firmware or hardware yet. That's step 3 (firmware on a
  fake clock) and step 4 (LTC capture and decoding).

## Layout

| Path | Contents |
|---|---|
| `design.md` | Design: protocol, module and app behaviour, hardware, build order |
| `sim/sim_audio_node.py` | Simulated box for the mesh simulator (node kind `audio`) |

The box firmware will go here too. Code the box shares with the other
devices (`nfcp.py`, `mesh.py`, `bridge_radio.py` and so on) stays in
`../main/`, and the box's install script copies files from both folders.

## Running the simulator scenario

From the repository root:

    py -3 scripts/sim/mesh_sim.py --scenario audio_chase
