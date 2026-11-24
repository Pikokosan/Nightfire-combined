# Audio Box and Timecode Sync Design

Status: draft, 2026-11-24. Steps 1 (module side) and 2 (bridge and app) are
implemented and tested in unit tests and the simulator; nothing has run on
hardware, and there is no audio box yet. See Build order.

An audio box is a new node on the mesh. It plays a show's soundtrack and
broadcasts the show position, and armed field modules fire their cue
scripts in time with the music. The bridge stays in command: it loads,
arms, starts and stops. The audio box only supplies the clock.

## Goals

- **Cues follow the music**, not the bridge's clock. If the soundtrack starts
  late, pauses or plays slightly fast, the cues follow it.
- **The box is triggered from the bridge.** The app picks a track and
  starts it through the bridge, like any other command.
- **±10 ms between modules and the music** (the target), at the mesh's
  default 2 hops.
- **The box adds no new way to fire.** It cannot arm, start, step or fire
  anything. Without a START from the bridge naming it, its broadcasts do
  nothing.
- **Fail-stop.** If the music or the operator link goes away, modules stop.

Out of scope for now:
- more than one audio box in a show;
- SMPTE input from outside equipment (a mixing desk, QLab) without the
  box playing the track itself. The decoder below would handle it, but the
  start and stop commands assume the box is the player;
- authentication (none today; see Safety).

## What exists today

| Piece | Behaviour | Where |
|---|---|---|
| TIME_SYNC (0x75) | Master clock to modules, corrected by the mesh age field and the arrival time (`rx_us`). | `module_script.py` `time_sync()` |
| SCRIPT_START (0x72) | `script_id(2) delay(2) offset(4) start_master_ms(4)`. Modules then run on their own clock. | `module_script.py` `handle_start()` |
| SCRIPT_HEARTBEAT (0x74) | `script_id(2) elapsed_ms(4)`. Modules use it only as a keep-alive (1500 ms timeout); `elapsed_ms` is ignored. | `handle_heartbeat()` |
| Timed frames | Frames for the commands in `MESH_TIMED_CMDS` (START, TIME_SYNC) carry an age field that every hold and relay adds to. | `mesh.py` |
| Sender identity | Every mesh frame carries a 4-byte `origin`, saved as `pkt["mesh_origin"]`. The NFCP source byte is no use: modules and the bridge both use node id 1. | `mesh.py`, `module_link.py` |
| Broadcast filter | Modules decode only the broadcast commands in a fixed list (ARM, DISARM, DISCOVER, KEEPALIVE, SCRIPT_START/STOP/HEARTBEAT, TIME_SYNC, PAIR). They don't check who sent them. | `nfcp.py` `_decode()` |
| Master bridge filter | The app may send SCRIPT_START only to one module at a time, and only as exactly 12 data bytes. | `usb_master_control.py` `_master_unique_valid()` |

So the mesh can already carry a node's own broadcasts with age correction.
What's missing is a command for "show position", modules that follow it,
and bridge rules that let the app use it.

## How a show runs

```
app ──USB──> bridge ──mesh──> field modules
                    └─mesh──> audio box ──mesh (SHOW_TIME)──> field modules
```

1. **Load.** The app loads the cue script into each module (SCRIPT_CLEAR /
   SCRIPT_LOAD, unchanged). It sends AUDIO_LOAD to the box with the track
   id. The box checks the track exists and ACKs; AUDIO_STATUS_REQ then
   gives its length.
2. **Arm.** Modules are armed as today. The box has nothing to arm.
3. **Start.** The bridge sends each module a SCRIPT_START in *chase* mode
   that names the box's unique id and the track id. Modules reply
   "waiting for timecode" and fire nothing yet. The bridge then sends
   AUDIO_PLAY to the box.
4. **Run.** While the track plays, the box broadcasts SHOW_TIME 4 times a
   second. Each module locks to the first one and then keeps
   correcting (see Module behaviour). The bridge keeps sending
   SCRIPT_HEARTBEAT as today.
5. **Stop.** SCRIPT_STOP from the bridge stops the modules, and AUDIO_STOP
   stops the box. The app sends both. Disarm and the bridge's ARM key work
   exactly as today.

## Protocol

Proposed command numbers, in the free 0x77–0x7E range (check against the
app's `bridge_codec.dart` before using them):

| Cmd | Name | Sent by | Kind | Data |
|---|---|---|---|---|
| 0x77 | SHOW_TIME | audio box | broadcast, timed | `track_id(2) position_ms(4) state(1)` |
| 0x79 | AUDIO_LOAD | bridge | unique, reliable | `track_id(2)` → ACK |
| 0x7A | AUDIO_PLAY | bridge | unique, reliable | `track_id(2) from_ms(4)` → ACK |
| 0x7B | AUDIO_STOP | bridge | unique, reliable | `track_id(2)` → ACK |
| 0x7C | AUDIO_STATUS_REQ | bridge | unique, reliable | none → AUDIO_STATUS_RSP |
| 0x7D | AUDIO_STATUS_RSP | audio box | response | `track_id(2) position_ms(4) state(1) level(1) [length_ms(4)]` |

The track length moved from AUDIO_LOAD's ACK to AUDIO_STATUS_RSP, so every
command gets the plain 2-byte ACK. `length_ms` is optional (8 or 12 bytes).

SHOW_TIME `state`: 0 stopped, 1 playing, 2 paused, 3 ended.

**SCRIPT_START, chase mode.** It's sent to one module at a time, as today,
as 18 bytes (`NFCP.build_script_start_chase()`):

```
script_id(2) audio_offset_ms(2, signed) unused(8) source_uid(4) track_id(2)
```

- 18 bytes or more means chase mode. The first 12 bytes keep their usual
  places so the length is the only switch; in chase mode the delay field
  carries the audio offset and the offset and master-time fields are
  unused (zero).
- `audio_offset_ms` is per show: positive fires every cue later than the
  music, negative earlier (amplifier delay, speaker distance).
- `source_uid` 0 is refused (NACK_INVALID_VALUE).
- A chase-aware module ACKs with **ACK_CHASE (0x06)** instead of ACK_OK.
  An older module reads the first 12 bytes, ACKs ACK_OK and would start
  on its own clock without the music. So **the app must treat an ACK_OK
  to a chase START as "not supported" and send that module SCRIPT_STOP**,
  and should refuse a chase show unless every target reports a firmware
  version that supports it (INFO_REQ).

**Done in step 1:**
- `mesh.py`: 0x77 is in `MESH_TIMED_CMDS`.
- `nfcp.py`: CMD_SHOW_TIME, the SHOW_* states, ACK_CHASE,
  `SCRIPT_START_CHASE_BYTES`, the two builders, and SHOW_TIME in the
  broadcast lists (builder and decoder).
- `module_link.py`: a SHOW_TIME doesn't refresh the master-link timer
  (`last_rx_ms`). Before this, every broadcast a module heard counted as
  hearing the master, so an audio box would have held off the link-loss
  auto-disarm. Any other node's broadcasts still count, as before.
- `module_script.py`: chase mode (Module behaviour, below).

**Done in step 2 (bridge usb-master-0.4.7):**
- `usb_master_control.py`: SCRIPT_START with 12 or 18 bytes; AUDIO_LOAD,
  AUDIO_PLAY, AUDIO_STOP and AUDIO_STATUS_REQ to one node, with their
  exact lengths. Chase START stays in `MASTER_ARMING` (ARM key on, module
  armed through this bridge). The AUDIO_* commands need neither, since
  they can't fire; AUDIO_STOP joins the stopping commands, so it goes out
  even after the app session has expired. SHOW_TIME from the app is
  refused: only the box sends it.
- `nfcp_frame.py`: the new command numbers and START lengths. `nfcp.py`:
  the AUDIO_* numbers and builders.
- The bridge passes the SHOW_TIME it hears on to the app over USB, as it
  does with every frame (4 small frames a second). The app ignores
  everything but module responses, so it takes the show position from
  AUDIO_STATUS_RSP instead.

**Done in step 2 (app):** see App behaviour, below.

**Rate: 4 SHOW_TIME a second (every 250 ms), not 10.** Measured in the
simulator (`audio_chase`, below): at 10 a second, the module 2 hops out
lost 4 bridge heartbeats in a row in 2 of 12 runs and stopped its show
(heartbeat timeout), because every message is 3 box repeats plus 2 copies
from each relaying module, and handling them keeps the modules busy. At
4 a second, 20 of 20 runs passed. Drift between messages is still
negligible (500 ppm over 250 ms is 0.13 ms), and 1500 ms of timecode loss
is 6 missed messages.

**Load.** Estimates:
- *Airtime.* A SHOW_TIME frame is 25 bytes (29 relayed) plus 8 bytes of
  radio overhead: about 0.3 ms at 1 Mbit/s. With 3 repeats and relays,
  about 1–3 ms per message.
- *CPU, and with it deaf time.* Handling a script heartbeat takes a module
  about 3 ms, and each duplicate copy about 0.7 ms
  ([mesh_radio_design.md](../docs/mesh_radio_design.md)). With several copies of
  each message heard, that's a few % of every module's time at 4 a second. The bridge hears SHOW_TIME too, and the
  bridge only listens while it's inside `recv()`, so this costs it
  reliable-delivery retries. If the bridge's background-receive firmware
  isn't in use by then, the bridge should drop SHOW_TIME right after the
  mesh header check, before decoding it.
- Nothing reliable waits on SHOW_TIME, so a lost message only means the
  next one is used.

## Module behaviour

State in `ScriptEngine` (`module_script.py`), used only after a chase
START:

| Field | Meaning |
|---|---|
| `chase_source`, `chase_track` | Who to follow, from the START |
| `chase` | `None`, `"wait"`, `"lock"` or `"pause"` |
| `chase_heard_ms` | Local time of the last SHOW_TIME accepted |
| `chase_offset_ms` | The START's audio offset |
| `chase_jumps` | Jumps seen; shown in the INFO status text with the state |

**Accept a SHOW_TIME only if** chase is on, the script is running,
`pkt["mesh_origin"] == chase_source`, the track id matches and firing is
allowed (armed, no fault). Anything else is ignored.

**Work out where the show is.** For an accepted SHOW_TIME:

```
sent_ms = arrival_ms(pkt) - age_ms(pkt)          # when the box read position
implied = sent_ms + chase_offset_ms - position_ms
error   = implied - start_ms
```

`arrival_ms()` and `age_ms()` already existed for TIME_SYNC. The box sends
with the mesh age counting from the moment it read the position.

**Lock** (first SHOW_TIME, resume after a pause, or a jump):
`start_ms = implied`, then cues more than `CHASE_LATE_MS` (100 ms) overdue
are skipped: `next_index` moves forward past them, never back. A cue less
than 100 ms overdue still fires at once, so a cue at 0 ms isn't lost to the
first message's delay.

**Locked: correct gently.**

| `abs(error)` | Action |
|---|---|
| ≤ 2 ms (`CHASE_DEADBAND_MS`) | Nothing (noise) |
| ≤ 250 ms (`CHASE_JUMP_MS`) | Move `start_ms` by `error / 2`, at most 10 ms (`CHASE_STEP_MS`) per message |
| > 250 ms | A jump: lock again at once |

**Jumps.**
- Forward: the cues jumped over are skipped (printed as `SCRIPT CHASE skip
  N cues`), not fired. Without this, `run()` would fire every overdue cue
  in one pass.
- Backward: `next_index` never moves back, so cues already fired never fire
  again; the module waits for the music to catch up.
- Each jump is printed (`SCRIPT CHASE jump N ms`) and counted.

**SHOW_TIME state.** Paused: hold, fire nothing, stay armed, and lock again
when it plays. A pause has no time limit while SHOW_TIME and the bridge
heartbeat keep coming; the operator can stop. Ended or stopped: stop the
script and outputs.

**Timecode loss.**
- Locked or paused: no accepted SHOW_TIME for `chase_timeout_ms` (1500 ms)
  stops the script and outputs (`SCRIPT stopped - timecode lost`). Until
  then the module freewheels on its own clock: at 500 ppm drift, 1.5 s of
  freewheeling costs under 1 ms.
- Waiting: no SHOW_TIME within `chase_wait_ms` (10 s) of the START stops it
  (`timecode never came`).

**The bridge heartbeat still applies.** Losing SCRIPT_HEARTBEAT stops the
show exactly as today. Both liveness checks must pass for cues to fire.

## App behaviour

`MasterController.startChase(boxUid:, trackId:, audioOffsetMs:)` in
`master_controller.dart` (step 2). No screen calls it yet; that is step 5.

**Before anything is sent** it refuses the show unless:
- the script is uploaded and every script module is armed;
- the bridge runs usb-master-0.4.7 or later (`bridgeSupportsChase()`);
- every script module's INFO says field-module 0.3.3+ or nrf-dummy 0.2.3+
  (`moduleSupportsChase()`);
- the box isn't one of the script modules, and the track id, box UID and
  offset are in range.

**Start.**
1. Bridge heartbeats begin (every 200 ms, as for a timed run): the modules
   need them while they wait for the music. No TIME_SYNC is sent.
2. Each module in turn: read its fire counter, send the chase START,
   then read its output state.
   - ACK_CHASE: it must then report armed and running.
   - ACK_OK: the module would run on its own clock. The app sends it
     SCRIPT_STOP and stops the show.
   - Anything else (a NACK): the show stops.
3. AUDIO_PLAY to the box.
4. If starting takes over 8 s (`chaseStartBudgetMs`), the show stops. The
   first modules give up after 10 s without SHOW_TIME.

Any failure here is an emergency stop: STOP and DISARM broadcasts, plus
AUDIO_STOP to the box.

**Running.**
- Once a second (every 5th heartbeat) the app reads AUDIO_STATUS_REQ.
  The position it reports drives the app's elapsed time.
- The run ends with an emergency stop if:
  - 3 reads in a row go unanswered;
  - the box reports another track;
  - the box reports `stopped`.
- `paused` keeps the run going. `ended` finishes it:
  1. STOP broadcasts.
  2. Each module's fire counter is read. The cues that didn't fire
     (jumped past, or timecode lost) are counted and shown.
  3. Every module is disarmed and confirms it, as for a timed run.
- The modules don't depend on these reads: they follow SHOW_TIME and stop
  on their own. The reads only tell the app where the show is and when it
  is over.

**Operator controls.**
- Pause is refused: the music sets the time, so a resume couldn't line up
  with it.
- Stop halts the modules (STOP broadcast, then a unique STOP to each one)
  and then sends AUDIO_STOP. The modules stay armed.
- Emergency stop and disarm work as for any run, and also send AUDIO_STOP.

## Audio box

**Hardware.**
- nice!nano (nRF52840) running the mesh code shared with the field modules.
  It pairs like a field module and has its own unique id.
- Any audio player that can start a chosen track on command (UART or GPIO).
- The player's right channel (LTC, from the track) goes back into the nRF:
  AC coupling, bias to mid-supply, then the nRF COMP peripheral or an
  external comparator. The left channel goes to the amplifier.
- Optional: a level detect on the left channel, so the box can report "no
  audio" (the `level` byte in AUDIO_STATUS_REQ).

**Why decode its own output.** The position the box sends is then what
the speakers are actually playing, not what the player says it's doing. If
playback stalls or the cable comes out, the timecode stops, and the modules
stop. Cheap MP3 players report position only in whole seconds, if at all.
A player that reports its exact sample position could skip the decoder,
but loses that check.

**LTC decoding.**
- SMPTE LTC is 80 bits per frame, biphase mark: the signal flips at every
  bit boundary, and flips again mid-bit for a 1. At 30 fps that's 2400
  bit/s, so up to 4800 edges/s.
- Edge times are captured by hardware (COMP → PPI → TIMER capture into a
  buffer) in native C, next to `nrf_radio`. **Not** Python pin interrupts:
  that would load the CPU and add deaf time (see
  [mesh_radio_design.md](../docs/mesh_radio_design.md)).
- The end of each frame's sync word (bits 64–79, `0x3FFD`) is the reference
  instant. Its timestamp is accurate to well under 1 ms, even though a
  frame lasts 33 ms.
- Position = frame timecode as ms, plus the time since that sync edge.
- The track's timecode should start at 00:00:00:00 at the show's zero, or
  the box is told the starting value with the track.

**Sending SHOW_TIME.** The position must match the moment the frame goes
out, the same way the bridge stamps the age of timed frames. The box reads
the position at a recorded `ticks_us` and adds the time from then until
the send.

**Box firmware.** A new `audio_main.py` built from the field module's
parts (`module_link.py`, mesh, pairing, OTA, INFO) with no fire outputs,
no ARM handling and no script engine. It sends only SHOW_TIME and replies.

## Safety

- **The box cannot start or fire anything.** Modules use SHOW_TIME only
  after a chase START from the bridge, and only while armed.
- **Only the named source counts**, checked by mesh origin. A second box,
  or one replaying old traffic, is ignored.
- **No catch-up bursts.** A forward jump skips cues, never fires them all at
  once.
- **No double firing.** A rewind never moves `next_index` back.
- **Two liveness checks:** bridge heartbeat (operator) and SHOW_TIME
  (music). Losing either stops the show.
- **Old modules.** The app refuses chase mode unless every target confirms
  it supports it (see Protocol).
- **What this doesn't fix.** Like every broadcast today, nothing proves a
  frame came from a real node. A node with the network id could send ARM
  or SHOW_TIME. This is no worse than today, but the box's firmware must
  never contain code that sends ARM, FIRE, SCRIPT_START or SCRIPT_STEP.

## Error budget

| Source | Size |
|---|---|
| LTC edge timestamp at the box | < 0.1 ms |
| Box: position read to radio send | corrected; leftover ~0.1 ms |
| Mesh age (holds, repeats, relays) | measured to µs, rounded to 1 ms |
| Module arrival stamp (`rx_us`) | < 0.1 ms |
| Module clock drift between messages (250 ms, 500 ppm) | 0.13 ms |
| Cue timing in the module loop (`wait_ms()`, GC pauses) | 1–5 ms (to measure) |
| Correction step (≤ 10 ms per message, halves the error) | settles within a few messages |

Expected total: a few ms, within the ±10 ms target. For comparison, sound
travels about 0.34 m per ms, so the music reaches someone 100 m away about
300 ms late.

## Testing

1. **Simulator (done).** `audio_box/sim/sim_audio_node.py` is a simulated
   box (node kind `audio`) that plays a track on its own drifting clock
   and broadcasts SHOW_TIME through the real mesh code. The `audio_chase`
   scenario has the box beside the bridge, two modules near it and one 2
   hops out; the box clock runs 150 ppm fast. It checks: chase START is
   ACKed with ACK_CHASE, nothing fires before the music, cues follow the
   music (within 10 ms) and each other (within 5 ms), a forward jump skips
   the cue it passes, and the box going quiet stops every module.
   Result over 20 seeds: no failures; cues within 2.3 ms of the music
   (mean 1.1 ms), modules within 2.3 ms of each other.

       py -3 scripts/sim/mesh_sim.py --scenario audio_chase
2. **Unit tests (done).** `scripts/tests/test_module_chase.py` (20 tests)
   runs `ScriptEngine` on a fake clock that wraps during the test: lock,
   audio offset, ±500 ppm drift over a minute, forward and backward jumps,
   gentle correction and its step limit, pause, end, timecode loss,
   never-came, other sources and tracks, bridge heartbeat, disarm, STOP,
   refused STARTs, and the plain START unchanged. Breaking each rule in
   the engine one at a time (11 mutations) makes a test fail.
   `test_module_disarm.py` checks that SHOW_TIME doesn't keep the master
   link alive on the field and dummy firmware.
   **Bridge and app (done, step 2).** `test_usb_master_control.py`: the
   chase START and AUDIO_* commands pass the bridge with exact lengths and
   nothing else; chase START needs the ARM key and an armed module;
   AUDIO_STOP goes out without the app. `master_controller_test.dart`
   (11 chase tests; 12 of 12 rule-breaking mutations caught) runs
   `startChase()` against a fake bridge, modules and
   box: frame contents and order, refusals for old modules and old
   bridges, a plain-ACK module stopped, a NACK, track end with unfired
   cues counted and everything disarmed, a box that stops answering,
   stops the track or plays another track, and operator stop. The Dart
   builders match the Python ones byte for byte
   (`export_master_android_goldens.py`).
3. **Bench, fake clock.** Box firmware broadcasting its own `ticks_ms` as
   the position. Two dummy modules on the bench; compare their FIRE prints
   (or a GPIO pin on a logic analyser) against each other and against the
   box's position. Then on field modules, with igniters disconnected.
4. **Bench, LTC.** Play a test track with LTC on the right channel and a
   click on the left at every cue. Record the click and a module output on
   two scope channels.
5. **A/B on the radio.** SHOW_TIME adds traffic, so rerun
   `reliable_ping_load.py` with and without it running.

## Build order

1. **Done (not on hardware):** module side: SHOW_TIME, chase START,
   correction and jump rules, timecode loss. Simulator and unit tests.
   Field module 0.3.3, dummy 0.2.3.
2. **Done (not on hardware):** bridge and app: bridge rules
   (usb-master-0.4.7), chase START and AUDIO_* builders, version checks,
   `MasterController.startChase()` and the run it drives (App behaviour).
   No screen uses it yet.
3. Audio box firmware with a fake clock, answering AUDIO_PLAY, AUDIO_STOP
   and AUDIO_STATUS_REQ; bench tests 3 and 5.
4. Native LTC capture and decoder; AUDIO_LOAD and real tracks; bench test 4.
5. App: track picker, box status, audio delay setting, jump and loss
   counts.

## Open questions

Decided in step 1, open to change:
- The audio offset is per show, in the START.
- After a pause, modules resume with the music.
- A backward jump holds until the music catches up; it doesn't stop the
  show.

Still open:
- Which player board? It needs track select over UART and a clean line
  output for the LTC channel.
- How does the box get tracks: SD card loaded by hand, or uploaded over
  USB from the app?
