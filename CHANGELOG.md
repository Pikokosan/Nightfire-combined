# Changelog

What changed, newest first, grouped by day and by part of the system. Each
entry names the commit it landed in. Add a line with every commit; put
work that isn't committed yet under **Unreleased**.

Parts: **Bridge** (USB radio dongle, `usb-master-*`), **Field** (field
module firmware), **Dummy** (`nrf-dummy`), **Audio box** (`audio-box`,
`audio_box/`), **Mesh** (radio layer shared by
all of them), **App** (Android controller), **Tools** (scripts, simulator,
installers), **Hardware**, **nRF image** (the MicroPython firmware with
the native `nrf_radio` driver that the bridge, field modules and dummies
run).

## Current versions

| Part | Version | Note |
|---|---|---|
| Bridge | usb-master-0.4.7 | Chase START and audio box commands (audio box sync). On the COM12 bridge since 2026-11-24 (installed over USB, permanent; backup in `dist/bridge_backup_20261124_131832`). |
| Field | field-module 0.3.6 | Dead `loop_once()` removed. On COM16 since 2026-11-25 (backup of 0.3.5 in `dist/field_backup_COM16_20261125`). Chase mode, SHOW_TIME log, RC calibration since 0.3.5, on COM16 since 2026-11-24 (backup of 0.3.2 in `dist/field_backup_COM16_20261124`). COM16 has no working 32 kHz crystal; calibrated it runs +389 ppm. |
| Dummy | nrf-dummy 0.2.6 | Dead `loop_once()` removed (0.2.6). Chase mode, SHOW_TIME log, RC calibration. Not on hardware (the only dummy, COM18, is now the audio box). |
| Audio box | audio-box 0.2.0 | Fake clock or the ESP32 player (`PLAYER = "uart"`, not on hardware). COM18 runs 0.1.1 (fake clock). On the old LCD dummy (COM18), which no longer runs nrf-dummy; restore with `upload_nrf_dummy.ps1 -Port COM18 -ConfigPath main/configs/dummy_lcd.py`. |
| Audio player | audio-player 0.1.0 | ESP32 + PCM5102A. Not on hardware yet. |
| App | 0.4.0 (build 4) | In its own repo, [nightfire-app](https://github.com/Pikokosan/nightfire-app), since 2026-11-25. App changes before then are below. |
| nRF image | 2026-11-06 build | On the field modules and dummies. The COM12 bridge runs the 2026-11-23 background-receive build: A/B retries 0.11% against 0.44%. |

## Unreleased

## 2026-11-25

### Field 0.3.6, Dummy 0.2.6 — remove the dead `loop_once()` — `e8129ce`
- **Removed:** `loop_once()` from `field_main.py` and `nrf_dummy_main.py`.
  Only the trace harness called it, and that went with the simulator
  (`627ca0c`). The main loops are unchanged. The bridge's
  `loop_once()` in `nrf_usb_monitor_main.py` is still used and stays.
- **On hardware:** field 0.3.6 is on COM16 (backup of 0.3.5 in
  `dist/field_backup_COM16_20261125`). `mesh_hw_smoke.py --uid D96E30DD`
  through the COM12 bridge: 11 of 11 checks pass (info, output status,
  resistance scan, script clear + load, disarm, 20 pings, 50 reliable pings
  with none failed). No ARM or FIRE was sent. Dummy 0.2.6 is not on
  hardware (there is no dummy board now).

### Tools — `mesh_hw_smoke.py --uid` — `e8129ce`
- **Added:** `--uid` picks the module to test. Without it the test takes
  the lowest uid that answers discover, which is now the audio box
  (`62A75130`), not the field module.

### Tools — remove the simulator and the tests built on it — `627ca0c`
- **Removed:** `scripts/sim/` (`mesh_sim.py`, `mesh_scenarios.py`,
  `firmware_trace_harness.py`, `bridge_trace_harness.py`,
  `PC_multi_module_emulator.py`), the audio box's simulated node
  (`audio_box/sim/`), `scripts/tests/test_module_disarm.py` (ran on the
  trace harness's fakes), `scripts/diagnostics/mesh_cpu_benchmark.py`
  (calibrated the simulator's CPU model), and the simulator's branding
  (`design/simulator-branding/`, `scripts/generate_simulator_brand_assets.ps1`).
  All of it is in git history up to `d6a428e`.
- **Changed:** README, `project_layout.md`, `scripts/README.md` and
  `audio_box/README.md` no longer list them; `mesh_radio_design.md` and
  `audio_box/design.md` keep the simulator results as a record, with a note
  that it is gone.
- **Not changed:** `loop_once()` in `field_main.py` and `nrf_dummy_main.py`
  was only called by the trace harness and is now unused. It is left in, so
  the firmware on the boards stays as it is.
- **Tests:** the 23 remaining test files pass. The disarm paths no longer
  have an offline test.

### Repo — the app moves to its own repo, nightfire-app — `e57c7b2`
- **Moved:** `apps/nightfire_controller/` is now the nightfire-app repo
  (`git subtree split`, so its 10 commits come along), checked out next to
  this one at `../../nightfire-app`. With it went the app build and icon
  scripts (now its `tool/`) and the app design notes
  (`android_command_module.md`, `android_master_port.md`,
  `script_workspace.md`, `ui_backlog.md`, now its `docs/`).
- **Changed:** `export_master_android_goldens.py` writes the app's NFCP
  fixtures into nightfire-app (`--app`, `NIGHTFIRE_APP`, or the sibling
  folder); the output is unchanged. `test_usb_master_bundle.py` reads the
  app's `bridge_codec.dart` from there and skips that check if the app
  isn't checked out.
- **Changed:** README, `project_layout.md`, `building_devices.md` and
  `scripts/README.md` point to nightfire-app; `.vscode/settings.json` no
  longer names the app's CMake folder; `.gitignore` ignores the old
  `/apps/` folder (local build output only).
- **Tests:** all 24 test files pass.

### Repo — on GitHub, GPL v3 licence — `2ed4732`
- **Added:** the GitHub repo (`github` remote,
  `git@github.com:Pikokosan/Nightfire-combined.git`). Its `main` now holds
  the Mesh work, merged with GitHub's initial commit.
- **Added:** `LICENSE`, GNU GPL v3, from GitHub's initial commit.
  `README.md` stays the project's own.

### Docs — audio box status and parts list — `e2a71bd`
- **Added:** `audio_box/STATUS.md`: where each step stands, what step 4
  still needs, the bench boards (COM12, COM16, COM18) and the parts to
  order (ESP32 DevKit, PCM5102A, SD card module, LTC input parts, a logic
  analyser, another nice!nano).

### Audio box audio-box 0.2.0 — drives the ESP32 player (audio box sync, step 4) — `e2a71bd`
- **Added:** `audio_box/main/player_link.py`, `UartPlayer`: the box's
  driver for the ESP32 player, with the fake player's interface. Chosen in
  the config: `PLAYER = "uart"` (UART 0, P0.06 TX / P0.08 RX, 115200); the
  default stays `"fake"`. LOAD asks the player for the track and its real
  length; PLAY checks the range and starts the box's clock when the player
  confirms; switching to an unloaded track sends STOP, LOAD, PLAY; STOP
  stops the box at once whatever the player says.
- **Safety (fail-stop):** STATUS every 500 ms. The player ending ends the
  track; the player stopped, on another track, more than 150 ms off the
  box's clock, or 2 polls unanswered: the box stops the track itself
  (`AUDIO player fault: ...`), sends 4 `stopped` SHOW_TIME and goes quiet.
- **Changed:** `audio_main.py` prints `AUDIO stopped` (not `ended`) when the
  player link stops a track; INFO says `player-clock` with the real player.
  The install script copies `player_link.py`; the default config lists the
  player settings.
- **Tests:** `scripts/tests/test_player_link.py` (14): the driver against
  the real player code through a fake UART on one clock; the box's
  position 1 ms behind the DAC; track end, early end, stop, dead and
  stalled players, busy, switching tracks; 11 of 11 mutations caught.
  `test_audio_box.py`: a player fault sends `stopped` (18 tests).
- **Not on hardware yet.** Until the native LTC capture, SHOW_TIME runs on
  the box's clock from the player's PLAY answer.

### Audio player audio-player 0.1.0 — ESP32 + PCM5102A firmware (audio box sync, step 4) — `e2a71bd`
- **Added:** `audio_box/player/player_main.py`, stock MicroPython on an
  ESP32 DevKit: plays `NNN.wav` (16-bit stereo, 44.1/48 kHz) from an SPI
  SD card into a PCM5102A over I2S (4 KB writes, 16 KB DMA buffer), and
  takes commands from the box's nRF over UART: PING, LIST, LOAD, PLAY
  (restart = jump), STOP, STATUS, with ERR replies and an unasked
  `ENDED n`. Drains the buffer with silence at the end; STOP and the end
  close I2S and mute the DAC (XSMT). Reported position within ~21 ms of
  the DAC (the nRF will check it against the LTC). Not on hardware yet.
- **Added:** `player_config.py` (pins), `player_boot.py`,
  `scripts/install/upload_audio_player.ps1`; wiring in `audio_box/README.md`.
- **Tests:** `scripts/tests/test_audio_player.py` (12) with a fake I2S that
  drains in real time: WAV headers (and refusals), every command, seeking
  (the LTC decoded from the output after `PLAY 7 1500` starts at 1500 ms
  ±1 ms), position against the DAC, track end, stop, jump, 44.1 kHz.
  10 of 10 rule-breaking mutations caught.

### Tools — LTC encoder, decoder and test tracks (audio box sync, step 4) — `e2a71bd`
- **Added:** `audio_box/main/ltc.py`: SMPTE LTC frame bits, `LtcEncoder`
  (edge times) and `LtcDecoder` (edge times from a wrapping µs counter in,
  (position_ms, sync-end time) out; 24/25/30 fps; learns the bit length;
  2 frames in sequence to lock; drops the lock on a gap). The reference
  for the nRF's native decoder to come.
- **Added:** `audio_box/tools/make_ltc_track.py`: stereo WAV test tracks,
  LTC on the right (-10 dBFS, ~25 µs edges), a click on the left at each
  cue; options for start frame, speed, noise and polarity.
- **Tests:** `scripts/tests/test_ltc.py` (16): frame bits and polarity
  bit, every rate, lock, counter wrap, ±3% speed, mid-frame start,
  dropout and relock, glitches, random edges never lock, and generated
  audio through an emulated comparator (48 and 44.1 kHz, inverted, -30
  dBFS, noise with hysteresis): sync-word end within 0.3 µs clean, 5 µs
  noisy.
- **Docs:** player decided (ESP32 + PCM5102A); step 4 plan in
  `audio_box/design.md`.
- **Fixed:** `.gitignore`'s `tools/` ignored every folder of that name, so
  `audio_box/tools/` would never have been committed; now `/tools/` (the
  local toolchain folder at the root only).

## 2026-11-24

### Field 0.3.5, Dummy 0.2.5, Audio box 0.1.1 — calibrate the 32 kHz RC oscillator — `57948bc`
- **Fixed:** on a board whose 32 kHz crystal doesn't run, `ticks_ms()` and
  `ticks_us()` followed the uncalibrated RC oscillator: COM16 (Pro Micro
  nRF52840 clone) ran 0.77% slow, about 460 ms a minute in a timed show.
  Its `LFCLKSTAT` showed the RC source although the crystal was requested,
  and a manual restart from the crystal never completed. The module link
  (`module_link.py`, shared by field, dummy and box) now finds this at boot,
  prints `LFCLK on RC (32 kHz crystal not running): calibrating every 4 s`,
  and calibrates the RC oscillator against the 32 MHz crystal every 4 s
  (`nrf_module_common.LfrcCalibrator`, a hardware task, skipped unless the
  32 MHz crystal runs). Boards with a working crystal are unchanged.
- **Measured:** COM16 -7,749 ppm before, +389 ppm with 0.3.5 (COM18 +59, both
  against the PC over 60 s). 10 chase shows on 0.3.5: the module's
  SHOW_TIME error went from a steady -4 ms to a median of 0 (270 messages,
  5–95% within ±1 ms, none past ±2 ms); every show behaved and the module
  was disarmed and confirmed.
- **Tools:** `test_audio_box_follow.py` judges timing on the module's own
  log (every SHOW_TIME within 5 ms, no cue over 10 ms past its own time)
  and only prints the USB print timing, which jitters by up to ~12 ms.
- **Tools:** `scripts/tests/test_lfrc_calibration.py` (5 tests); the field
  bundle exports `LfrcCalibrator`. Bench helper fix: sequence numbers wrap
  after 255, so a stale reply could be read as a new one (the ARM key check
  crashed once while waiting); each send now drops the old reply.
- **Installed:** COM16 runs field-module 0.3.5, COM18 audio-box 0.1.1.

### Field 0.3.4, Dummy 0.2.4 — chase SHOW_TIME log (audio box sync, bench) — `57948bc`
- **Added:** in chase mode the script engine keeps every SHOW_TIME it
  accepts (error against the module's clock before correcting, mesh age,
  handling delay; up to 48 a show) and prints them as three
  `SCRIPT CHASE log` lines when the show stops (`module_script.py`). Printed
  after the show, so it never delays a cue.
- **Measured (COM16, 10 shows):** 258 messages, error median -4 ms, 5–95%
  -5 to -2 ms, one +30 ms outlier; radio stamps steady to about ±2 ms.
- **Found:** COM16's clock runs 0.77% slow (-7,749 ppm against the PC over
  60 s; the box on COM18 +59 ppm). Chase mode follows it with a steady
  4 ms lag; a timed show would drift 460 ms a minute on that board. Cause
  not checked yet (likely the board's 32 kHz clock source).
- **Tools:** 2 new tests in `test_module_chase.py` (22 in all);
  `test_audio_box_follow.py` reads the log after each show and sums it up.
- **Installed:** COM16 runs field-module 0.3.4.

### Audio box audio-box 0.1.0 — firmware on a fake clock (audio box sync, step 3) — `57948bc`
- **Added:** `audio_box/main/audio_main.py`, the first box firmware. A mesh
  node built from the module parts (`module_link.py`: pairing, INFO,
  settings, OTA, relaying) with no outputs, ARM or script engine. A fake
  player on the box's own clock: every track id exists and is 10 min long
  (`FAKE_TRACK_MS`, or only those in `FAKE_TRACKS`); the position is exact
  to the µs across `ticks_us()` wraps.
- **Added:** AUDIO_LOAD, AUDIO_PLAY (start, or restart at `from_ms`: a
  jump), AUDIO_STOP (stops whatever plays, any track id) and
  AUDIO_STATUS_REQ (12-byte reply, `level` 0xFF until step 4).
- **Added:** SHOW_TIME at once on play, then every 250 ms, 3 copies each,
  its mesh age counting from the position read. At the track end or a stop,
  4 more messages say `ended` or `stopped`, then the box goes quiet.
- **Safety:** the box never claims to be armed. ARM, FIRE, scripts and any
  other unique command get NACK_INVALID_VALUE; DISARM is ACKed and output
  status says nothing is armed, so Disarm all includes it. A main loop error
  stops the music (the modules then stop within 1.5 s). A test checks the
  source never builds ARM, FIRE, SCRIPT_START or SCRIPT_STEP.
- **Added:** `audio_boot.py` (the board's `main.py`), `audio_box/configs/
  audio_box.py`, DISCOVER device type 0x30 with 0 cues.
- **Bench (2026-11-24):** installed on the old LCD dummy (COM18, uid
  0x62A75130) with the COM12 bridge on usb-master-0.4.7:
  `test_audio_box_bench.py` passed 19 of 19 (discovery, INFO, DISARM,
  output status, LOAD/PLAY/STOP, status, track end; SHOW_TIME counted on
  air by the bridge's radio: 11.4 frames a second over idle while playing,
  none after the end or STOP).
- **Bench 3b (2026-11-24):** the COM16 field module (0.3.3, igniters
  disconnected, every cue read open) followed the box in a chase show,
  `test_audio_box_follow.py`. Run 3: 21 of 21, cues within 3.7 ms of the
  box. Run 2: 18 of 21, the cues before the jump ~11.5 ms late (same
  offset on every cue, so the lock or the measurement, not the module's
  loop). Jump skip, stop and disarm worked in both. Then 10 shows on one
  key turn (`--shows`): every show behaved and the module was disarmed and
  confirmed, but cues were -10.6 to +15.4 ms from the box and no show had
  all cues within 10 ms. The box's own USB prints jitter by up to ±9 ms,
  so this method can't resolve the 10 ms goal; the module's clock also
  stepped by up to 12 ms, which suggests single SHOW_TIME messages 15-25
  ms off on real radios. Needs a measurement without USB (see the design
  doc). Radio A/B (test 5) not done.
- **Not yet:** the app's Arm all would fail on the box's NACK; step 5 must
  leave device type 0x30 out.

### Tools — audio box firmware tests and install (audio box sync, step 3) — `57948bc`
- **Added:** `scripts/tests/test_audio_box.py` (17 tests on a fake µs clock
  that wraps; 16 of 17 rule-breaking mutations caught, the 17th changes
  nothing because `module_link.py` already filters by target).
- **Added:** simulator node kind `audiobox` (the real firmware) and
  scenario `audio_box`: 59 of 60 seeds pass; the other lost one chase
  START ACK over 2 hops. Cues within 3.5 ms of the box.
- **Added:** `scripts/bench/test_audio_box_bench.py`: the box through the
  bridge, with the box's own commands only (never ARM, FIRE or START).
- **Added:** `scripts/bench/test_audio_box_follow.py` (arms a module):
  bench test 3b, a module following the box; refuses to arm unless every
  cue reads open, needs the bridge's ARM key, always disarms and confirms.
  `--shows N` runs N shows on one key turn, `--log` saves both boards'
  prints.
- **Fixed (docs):** the design doc said the bridge passes SHOW_TIME on to
  the app over USB; it passes only module replies.
- **Added:** `scripts/install/upload_audio_box.ps1` (shared files from
  `main/`, the box's from `audio_box/main/`) and the VS Code task "Audio
  box: upload".
- **Docs:** `audio_box/design.md` (box firmware, tests, build order) and
  `audio_box/README.md`; `scripts/README.md`.

### Bridge usb-master-0.4.7 — chase START and audio box commands (audio box sync, step 2) — `67aa272`
- **Added:** the app may send SCRIPT_START with 18 bytes (chase mode) as
  well as 12. It is still an arming command: ARM key on, and a module
  this bridge armed.
- **Added:** AUDIO_LOAD (0x79), AUDIO_PLAY (0x7A), AUDIO_STOP (0x7B) and
  AUDIO_STATUS_REQ (0x7C) to one node, each with its exact length. None
  of them needs the ARM key or an armed module, since they can't fire.
  AUDIO_STOP is a stopping command, so it goes out even after the app
  session has expired.
- **Mesh / protocol:** `nfcp_frame.py` gains the new command numbers and
  START lengths; `nfcp.py` gains the AUDIO_* numbers (AUDIO_STATUS_RSP
  0x7D) and builders.
- **Note:** not installed on the COM12 bridge yet. Build it with
  `py -3 scripts/build/build_usb_master_bundle.py` (28,827 bytes).

### App — follow an audio box (audio box sync, step 2) — `67aa272`
- **Added:** `MasterController.startChase()`.
  - It refuses before sending anything unless the bridge is
    usb-master-0.4.7+ and every script module reports field-module 0.3.3+
    or nrf-dummy 0.2.3+.
  - Each module gets a chase START. A module that answers ACK_OK (it would
    run on its own clock) is sent STOP, and the show stops.
  - Then the box is told to play.
  - Once a second it reads the box's status. That gives the show position.
    A track end finishes the run: it counts the cues that didn't fire and
    disarms everything.
  - A box that stops answering (3 reads), stops the track or plays another
    track ends the run with an emergency stop.
  - Pause is refused. Stop halts the modules, then the music. Emergency
    stop also sends AUDIO_STOP.
  - No screen uses it yet (step 5).
- **Added:** codec: `scriptStartChase()`, `audioPlay()`, `audioStop()`,
  `AudioStatus`, `bridgeSupportsChase()`, `moduleSupportsChase()`;
  `moduleQuery()` allows 0x7C.
- **Tools:** 11 chase tests in `master_controller_test.dart`; breaking
  each chase rule in the controller one at a time (12 mutations) makes one
  fail. Its fake radio now handles reliable delivery (0x1A) and reports
  module app/firmware in INFO. The golden frames gain the chase START and the
  audio commands (`export_master_android_goldens.py`).
  `test_usb_master_control.py`: 2 new tests.
- **Docs:** `audio_box/design.md`: App behaviour section, protocol
  table (AUDIO_STATUS_RSP; the track length moved there from
  AUDIO_LOAD's ACK), step 2 marked done.

### Tools — audio box gets its own folder — `67aa272`
- **Moved:** `docs/audio_sync_design.md` → `audio_box/design.md`;
  `scripts/sim/sim_audio_node.py` → `audio_box/sim/sim_audio_node.py`
  (the simulator also looks in `audio_box/sim/`). New
  `audio_box/README.md`. The box firmware (step 3) will go in
  `audio_box/`; shared code stays in `main/`. References updated in code
  comments, `README.md` and `docs/project_layout.md`.

### Field 0.3.3, Dummy 0.2.3 — chase mode (audio box sync, step 1) — `2cac449`
- **Added:** chase mode in the script engine (`module_script.py`), from
  `docs/audio_sync_design.md`. A SCRIPT_START of 18 bytes names an audio
  box (`source_uid`) and a track; the module ACKs with the new code
  ACK_CHASE (0x06), fires nothing until that box's SHOW_TIME arrives, then
  fires its cues on the box's play position. Small differences are
  corrected gently (half the error, at most 10 ms per message); a
  difference over 250 ms is a jump: cues jumped over are skipped, never
  fired, and cues already fired never fire again. Paused holds, ended
  stops. No SHOW_TIME for 1.5 s once running, or 10 s after the START,
  stops the show and outputs. The bridge heartbeat is still needed as
  before. A per-show audio offset (signed ms) sits in the START's delay
  field.
- **Added:** NFCP CMD_SHOW_TIME (0x77): `track_id(2) position_ms(4)
  state(1)`, broadcast, timed (mesh age field); the SHOW_* states,
  ACK_CHASE, `NFCP.build_script_start_chase()` and
  `build_show_time_broadcast()` (`nfcp.py`). **Mesh:** 0x77 added to
  `MESH_TIMED_CMDS`.
- **Fixed (safety, before it could matter):** a module counted every
  broadcast it heard as hearing the master, so an audio box's SHOW_TIME
  would have held off the link-loss auto-disarm. SHOW_TIME no longer
  refreshes the master-link timer (`module_link.py`).
- **Unchanged:** shows without chase. The firmware trace harness gives the
  same trace before and after for the field and dummy firmware.
- **Tools:** `scripts/tests/test_module_chase.py` (20 tests on a fake clock
  that wraps; 11 of 11 rule-breaking mutations caught);
  `test_module_disarm.py` checks SHOW_TIME doesn't keep the master link on
  either firmware. Simulator: node kind `audio`
  (`scripts/sim/sim_audio_node.py`, a simulated box on its own drifting
  clock) and scenario `audio_chase` (box beside the bridge, one module 2
  hops out, forward jump, box going quiet); `App.ack_code()`;
  `modules_of()` leaves the audio node out.
- **Measured (simulator):** at 10 SHOW_TIME a second the module 2 hops out
  missed 4 bridge heartbeats in a row and stopped in 2 of 12 runs; at 4 a
  second (the default now) 20 of 20 runs passed, cues within 2.3 ms of the
  music (mean 1.1 ms) and within 2.3 ms of each other.
- **Note:** existing scenarios: deep3 and crowd fail the same checks as
  before (4 and 5 failures over seeds 2-5). field: 6 failures over seeds
  2-9 against 5 before, all in the cue-spread check of a plain script
  start, which already failed on some seeds; it moved because
  `handle_start` makes one more call, which the simulator charges.
- **Note:** not on hardware yet. The bridge (`usb_master_control.py`)
  still refuses an 18-byte SCRIPT_START from the app; that is step 2.

## 2026-11-23

### nRF image 2026-11-23 (background receive) — `8ab3ba6`
- **Changed:** the radio listens all the time, not only while Python waits
  in `nrf_radio.recv()`. Before, `recv()` turned the receiver on at entry
  and off on return, so a board was deaf whenever its Python loop did
  anything else: about half the time on the COM12 bridge. Now the RADIO
  interrupt keeps packets that arrive in between in a ring of 4 and
  re-arms the receiver (about 40 us); the next `recv()` returns the oldest
  at once. The radio goes back into RX after `init()`, `recv()`, `send()`,
  `channel()` and `noise_sample()`. The Python API is unchanged, so `main/`
  code needs no change.
- **Added:** `stats()` keys `rx_dropped` (good packets dropped because 4
  were already held) and `rx_held` (held right now). `clear_stats()`
  clears `rx_dropped`. The bridge's `CMD_GET_STATS` reply does not carry
  them yet; read them in a REPL.
- **Changed:** a CRC error no longer ends `recv()` early with `None`; it is
  counted and `recv()` keeps waiting until its timeout.
- **Changed:** `send(cca_ms=...)` samples the channel with the listening
  receiver, and a packet on the air counts as busy. Before, a packet that
  arrived during the check stopped the receiver and the check passed as
  clear. Without CCA, `send()` lets a packet already on the air finish
  (at most about 2 ms) and keeps it before transmitting.
- **Changed:** `init()` and `channel()` drop held packets; `noise_sample()`
  keeps them and resumes listening on the previous channel. A packet heard
  on the sampled channel no longer stops the sampling receiver.
- **Fixed:** the driver's busy-wait guards could not report a timeout (the
  counter wrapped past zero), so a radio that never finished a transmit
  counted as `tx_ok` instead of raising `ETIMEDOUT`.
- **Note:** same filesystem layout (`FS_SIZE = 0x80000`,
  `nicenano_1M_256k.ld`), so a reflash keeps the board's files. The
  2026-11-06 image is kept as
  `firmware/nrf52840/nightfire_nicenano_micropython_2026-11-06_baseline.uf2`
  for A/B runs and to go back. How to flash the COM12 bridge is in
  `firmware/micropython/README.md`.
- **Note:** on the COM12 bridge only so far. Field modules and dummies
  stay on the 2026-11-06 image until they are flashed one at a time.
- **Note:** flashed to the COM12 bench bridge on 2026-11-23. It came back
  on COM12 with its files (`main.py`, `nrf_bridge_config.py`, the 28,548-byte
  usb-master-0.4.6 bundle), `os.uname()` shows the 2026-11-23 build, and
  `nrf_radio.stats()` has `rx_dropped` (0).
- **Note:** `mesh_hw_smoke.py --bridge COM12` on the new image, 2026-11-23:
  11 of 11 checks pass with field module D96E30DD (ping x20 lost 0, median
  61 ms; reliable ping x50 failed 0, median 104 ms; tx_ok 249, rx_ok 243,
  no CRC or length errors).
- **Measured:** A/B on the COM12 bridge with field module D96E30DD,
  2026-11-23, one session, 300 reliable pings per run (`reliable_ping_load.py`),
  new and old image alternating, bridge app usb-master-0.4.6 on both:

  | Image | Retries (3 runs) | Median | p95 | Max | Radio packets the bridge took in per run |
  |---|---|---|---|---|---|
  | 2026-11-23 (background receive) | 1 of 900 (0.11%): 0, 0, 1 | 65 ms | 95-96 ms | 112-574 ms | 917-919 for 942-945 sent |
  | 2026-11-06 | 4 of 900 (0.44%): 2, 1, 1 | 65 ms | 97-98 ms | 555-563 ms | 781-804 for 948-951 sent |

  The bridge takes in about 97% as many packets as it sends with the new
  image against about 83% with the old: the module's reply copies that
  used to land while the bridge was busy are now kept. Fewer of them are
  lost, so fewer deliveries need a retry. The median is unchanged because
  the first copy that does arrive answers the ping either way.
  `rx_dropped` stayed 0 and no CRC or length errors on either image. The
  bridge is left on the new image.

### Bridge usb-master-0.4.6 / usb-monitor-0.3.4 — `0913868`
- **Changed:** NFCP frames are encoded and decoded in one place,
  `main/nfcp_frame.py`, with named command numbers. Before, the master
  policy, the monitor policy, the keepalive and the pairing offer each had
  their own copy of the escape/CRC code and used bare numbers. A test keeps
  its values and frames identical to `nfcp.py`.
- **Changed:** the main loop is a `UsbBridge` class with one handler per USB
  command instead of one long `if`/`elif` chain. The "app session and
  nothing armed" check is one function (`may_change`) instead of three
  hand-written copies. Behaviour is unchanged: the new bridge trace harness
  gives the same trace before and after in master and monitor mode (apart
  from the version string).
- **Fixed:** the bridge hears the radio only while it waits in
  `nrf_radio.recv()`, and it spent about half of each main-loop pass doing
  other work. Two of the biggest pieces are gone: the status LED colours
  (about 3 ms) are worked out only when something they show changes, and a
  module reply is fully decoded only if it can be the ACK of a DISARM.
  Measured on the COM12 bridge with one field module, 300 reliable pings
  per run, old and new alternating: retries 0.4% (7 of 1,800) against 1.1%
  (23 of 2,100) for 0.4.5, median round trip 66-70 ms against 72-82 ms,
  p95 about 103 ms against 113-122 ms. The cleanup alone (faster loop
  passes, same work) had made it worse, 3-5% retries, by shifting when the
  bridge was deaf; that version was never released.
- **Note:** a bridge installed from source files (not the compiled bundle)
  now needs `nfcp_frame.py`; `install_usb_bridge.py` copies it. The
  compiled bundle is 28,548 bytes (was 26,666).
- **Fixed:** the bridge refused `mesh.py` in a file update, although the app
  offers it. The bridge now accepts `mesh.py` and `nfcp_frame.py`.

### App — `0913868`
- **Changed:** the firmware page also accepts `nfcp_frame.py`.

### Tools — `0913868`
- **Added:** `scripts/sim/bridge_trace_harness.py` runs the USB bridge on
  fake hardware through every USB command, session changes, arming,
  pairing, maintenance and a main-loop error, and writes a trace to compare.
- **Added:** `scripts/diagnostics/reliable_ping_load.py` loads a bridge with
  reliable pings to one module and reports retries and round trips, to A/B
  two bridge firmwares on the bench.
- **Added:** `scripts/tests/test_nfcp_frame.py`; the bundle test now also
  checks that no top-level name is defined in two bridge files and that the
  bridge accepts every file the app may send.

### Field field-module 0.3.2 — `87a8f5d`
- **Changed:** one `disarm()` now makes the module safe on every path: the
  disarm command, master link timeout, startup fault, a refused arm and a
  main-loop error. Each path stops the script, clears every cue output,
  ends any pre-test and drives the 595 drivers, LEDs and ARM_CONTROL to
  SAFE. Before, the five copies differed slightly.
- **Fixed:** an arm refused because of a startup fault left the script
  running and the cue mask set (outputs were already disabled, and the
  fault blocked firing). Disarm paths other than a startup fault didn't
  switch PRE_TEST off. Every arm now starts from SAFE, re-arms included.

### Dummy nrf-dummy 0.2.2 — `87a8f5d`
- **Changed:** the disarm command and main-loop errors use `disarm()`, like
  the link timeout already did.

### Tools — `87a8f5d`
- **Added:** `scripts/tests/test_module_disarm.py` drives every disarm path
  of both firmwares on the trace harness's fake hardware and checks the
  module ends up SAFE.
- **Added:** simulator branding (icons and splash screens in
  `design/simulator-branding/final/`) and
  `scripts/generate_simulator_brand_assets.ps1`, which makes them from a
  logo and a splash image.

## 2026-11-11

### Field field-module 0.3.1 — `586ec11`
- **Added:** adjustable cue hold time (how long a fired cue output stays
  on), 1-2000 ms. New unique-only radio command 0x29 reads it (empty) or
  sets it (2 bytes, ms); the reply 0x2A carries the value in use. A new
  value is saved in `field_fire_pulse.bin` and replaces `FIRE_PULSE_MS`
  from the config at boot. Refused while armed, while a script runs or a
  cue is on, or out of range.
- **Fixed:** cue outputs were held on for up to the 50 ms radio receive
  timeout rather than `FIRE_PULSE_MS` (5 ms), because the main loop only
  switched them off between receive waits. The receive wait now ends when
  the earliest active cue is due, so the hold time is kept without
  blocking. With the default 5 ms, real pulses are now shorter than before.
- **Added:** pairing with a bridge. LCD menu → Pair, or UP held for a
  second at power-up, listens on the pairing network 0x7A1B / channel 80 for
  2 minutes. A bridge's offer (broadcast 0x34) sets and saves the module's
  network and channel; the module replies 0x35 and moves to them. Its group
  is kept. Disarmed only; arming or leaving the pairing screen ends it.
- **Fixed:** BACK did nothing on the Pro Micro board. It is on P0.10, an
  NFC antenna pin that isn't GPIO until `UICR.NFCPINS` is cleared (the same
  goes for ARM_CONTROL on P0.09). The firmware now clears it at boot when
  the config uses either pin, and restarts once. If the write doesn't take,
  it reports a startup fault instead of restarting again.
- **Fixed:** LCD buttons were slow and missed short taps: a press only
  counted after two readings 80 ms apart, and buttons were read once per
  main-loop pass (up to the 50 ms radio wait). A pin interrupt now latches
  each press and it counts at once; then the button is ignored for the
  debounce time, so release bounce isn't a second press. The second row of
  a screen is drawn on the next pass instead of after another radio wait.

### Dummy nrf-dummy 0.2.1 — `586ec11`
- **Added:** answers 0x29/0x2A like the field module (kept until restart).
- **Added:** pairing, the same as the field module (shared link and LCD code).

### Bridge usb-master-0.4.5 — `586ec11`
- **Added:** master mode relays the cue hold time command 0x29 (read, or a
  2-byte set) and refuses a set while modules may be armed. Capability bit
  128 says so.
- **Added:** pairing window, command 0x1C (1-30 s; 0 ends it). The radio
  moves to the pairing network and channel and offers this bridge's network
  and channel every 300 ms, then goes back. Field sends and network/channel
  changes are refused during the window. Needs an app session and nothing
  that may be armed. Capability bit 4.

### Bridge usb-master-0.4.4 — `586ec11`
- **Added:** adjustable status LED brightness, 1-255
  (`STATUS_LED_BRIGHTNESS`, default 12, which looks the same as before).
  New command 0x1B reads or sets it, and capability bit 64 says that the
  bridge supports it. A new value is saved in `nrf_bridge_config.py`, and
  it's refused while modules may be armed. Reinstalls keep the bridge's
  value.

### App — `586ec11`
- **Added:** Modules → **Pair modules**: opens a 10 s pairing window on the
  bridge, lists the modules that paired, then runs discovery. Disabled while
  modules are armed; needs bridge usb-master-0.4.5.
- **Removed:** the Battery section of Settings (pack type and voltage
  calibration). Gauges still use any calibration saved earlier.
- **Added:** Settings → Modules → Cue hold time (1-2000 ms, saved in the
  app) with **Send to modules**, which sends it to every discovered module
  and lists each module's confirmed value, or why it didn't take (armed,
  busy, no reply from field firmware before 0.3.1). Needs bridge
  usb-master-0.4.5; disabled while modules are armed or a script runs.
- **Tests:** the script picker lifecycle tests now step through the real
  Android states (resumed, inactive, hidden, paused); the text field's
  lifecycle listener asserts on skipped steps.
- **Added:** Settings → Command module → Status LED brightness slider
  (1-100 %, square curve so most of the travel is at the dim end; 22 % is
  the old level). Disabled on older bridges and while modules are armed.

### Tools — `586ec11`
- **Added:** `set_bridge_leds.py --brightness`, and
  `scripts/diagnostics/probe_led_brightness.py` to read or set it over USB.
- **Fixed:** `install_usb_bridge.py` ran the bridge out of memory reading
  back the 25 KB bundle (backup and verify) in one piece; it now reads files
  in 512-byte chunks.

Checked on the COM12 bench bridge: 0.4.4 installed, brightness read as 12,
set to 120 and saved in `nrf_bridge_config.py`, then set back to 12. On
the emulator through the relay, the slider read 22 % from the bridge, and
moving it to 60 % saved level 92 on the bridge.

Cue hold time: offline tests only (Python: field command, saved value,
capped receive wait, bridge relay policy; app: send to all modules,
per-module failures, refusals, Settings field). Not yet run on a field
module or the bridge.

Pairing: checked on the Pro Micro field module and the bridge from the
tablet app; the module paired and was then discovered.

Buttons and NFC pins: on the Pro Micro field module (field_main.mpy 53,062
bytes) `UICR.NFCPINS` read 0xFFFFFFFE after the first boot. A trace of the
real main loop showed every OK, UP and DOWN press acted on once, on the first
pass after it (passes about 53 ms, LCD row writes up to 30 ms). BACK did not
reach P0.10 at all; that was a wiring fault on the test board, now fixed.

## 2026-11-10

### Tools — `bdb59d7`
- **Fixed:** the armed resistance scan tests stubbed the old
  `script_running` flag; the field firmware checks the shared script
  engine's `script.running`. Added a test that a running script blocks the
  scan. All offline tests pass.

### Field — `b9c64cd`
- **Changed:** buttons are read active high with no internal pull-up: on
  the production boards they pass through an inverting 74HC14
  (`BUTTON_ACTIVE_VALUE`, `BUTTON_INTERNAL_PULL_UP` in both field configs).
- **Added:** arm voltage check settings in both field configs (enabled,
  50 ms settle, 500 mV minimum, 16 samples).
- **Changed:** a cue counts as shorted only at 0.0 ohm
  (`RESISTANCE_SHORT_MAX_DECI_OHM = 0`, field configs and ESP32 master).
- **Changed:** `upload_field_module.ps1` compiles with `mpy-cross -O3`,
  installs the node config as `nrf_node_config.mpy`, and removes a leftover
  `field_hardware_test.py`.

### ESP32 master — `b9c64cd`
- **Added:** the manual fire grid shows every cue of the largest module and
  scrolls; the Trellis keypad and its LEDs follow the visible 16 cues.
- **Changed:** manual fire debounce is per module and cue. Trellis presses
  rely on the key scanner's release-gated debounce, so quick presses on
  different keys are no longer dropped; LVGL and LED updates stay out of
  the key-read path.
- **Changed:** the module database is kept in memory only, and the old
  `nfcp_devices.json` is deleted at boot.
- **Changed:** the continuity page shows NO COMM for a module that has
  gone offline instead of its last readings.

### Tools — `b9c64cd`
- **Added:** `main/trellis_hardware_test.py` and
  `scripts/install/upload_trellis_test.ps1` (standalone 4x4 Trellis
  keypad/LED test); `scripts/diagnostics/capture_field_console.py` and
  `probe_radio_io_api.py`; offline tests for the manual fire viewport,
  offline cues and the in-memory device database; more field resistance
  tests.

### Docs — `b9c64cd`
- **Changed:** field LCD and pinout docs describe the 74HC14 button inputs;
  UI backlog notes the battery settings that are done; removed a reference
  to an example script file that doesn't exist.

### App — `506fea1`
- **Changed:** the bar at the top shows the whole system's arm state instead
  of the app's arm acknowledgements: SYSTEM SAFE (green), COMMAND MODULE ARMED
  (orange, key on ARM), SYSTEM ARMED • n modules (red, key on ARM and modules
  confirmed ARM), or ARM STATE UNKNOWN. Errors take over the bar with a
  reason underneath: ARM KEY ERROR (key on at start-up), COMMAND MODULE FAULT
  (switch fault, radio unavailable, RF heartbeat failing, app heartbeat
  expired), ARM KEY UNKNOWN, MODULES ARMED, KEY SAFE, STOPPING / STOPPED, and
  the last arm/fire operation that failed. Checked SAFE and COMMAND MODULE
  ARMED on the emulator with the bench bridge.

### Bridge usb-master-0.4.3 — `552617a`
- **Added:** ARM key safety latch. If the key is on ARM when the bridge
  powers up or when the app connects, the bridge refuses ARM/FIRE/START/STEP
  until the key has been turned to SAFE. Pixel 1 flashes yellow meanwhile,
  and status fault bit 32 reports it (the red fault pixel ignores this bit).
  A reconnect with a new session tag counts as a new connection, even within
  the 3 s timeout. Checked on the COM12 bench bridge: power-on on ARM
  (yellow, ARM refused), key to SAFE then ARM (ARM accepted), quick
  reconnect on ARM (latched again).

### Tools — `552617a`
- **Added:** `scripts/diagnostics/probe_arm_key_latch.py`: watch the latch and
  test whether the bridge accepts ARM (to a UID that doesn't exist).

### App — `552617a`
- **Added:** "Turn the ARM key off" popup while the latch is set; Arm buttons
  are disabled and arming refuses with the reason. It closes by itself when
  the key reaches SAFE, and can be hidden (arming stays blocked). The app
  also latches from its own first status, covering older bridge firmware.

## 2026-11-09

### Bridge — `52d2964`
- **Changed:** the COM12 bench bridge's LEDs are now WS2811s. They showed all
  white with the WS2812B settings (250 ns T0H read as 1). Now RGB order and
  timing (150, 1100, 550, 700); the test pattern was correct. The repository
  default (`main/configs/bridge_usb.py`) is the same.

### Tools — `52d2964`
- **Added:** `scripts/install/set_bridge_leds.py`: shows a bridge's LED
  settings, lights a red/green/blue/white test pattern with chosen colour
  order and timing, and saves them on the bridge. Presets for WS2812B,
  WS2811 (800 kHz and 400 kHz) and SK6812.
- **Changed:** `flash_nrf52840_uf2.ps1` now flashes the repository's prebuilt
  image by default and finds the bootloader drive by itself.
- **Changed:** files reorganised (no firmware behaviour change; offline tests,
  simulator runs and firmware traces are identical before and after):
  - `scripts/` split into `install/`, `build/`, `tests/` (offline),
    `bench/` (real hardware), `diagnostics/`, `sim/`, `android/`, `lib/`;
  - upload scripts lost their COM numbers: `upload_field_module.ps1`,
    `upload_esp32_master.ps1`, `upload_esp32_master_bridge.ps1` (was
    `upload_nrf_bridge.ps1`), `upload_lcd_ui_test.ps1`,
    `upload_field_hardware_test.ps1`;
  - device configs moved to `main/configs/` with plain names
    (`bridge_usb.py`, `field_ms88sfa.py`, `dummy_lcd.py`...);
  - `nrf52840/` (MicroPython board and `nrf_radio` source) moved to
    `firmware/micropython/`;
  - removed: unused `nrf_node_config_com15*.py`, `1v8broken.hex`.

### Docs — `52d2964`
- **Added:** `docs/building_devices.md`, step-by-step build and programming
  guide for the bridge, field module, dummy and app.
- **Added:** `docs/bridge_status_leds.md`: LED settings, presets, and how to
  fix all-white or wrong-colour LEDs.
- **Changed:** README rewritten as an index; ESP32 master material moved to
  `docs/esp32_master.md`, field LCD material to `docs/field_module_lcd.md`;
  `docs/project_layout.md` and `scripts/README.md` rewritten.

## 2026-11-08

### Mesh — `072b095`
- **Fixed:** a far module flipped between a relay and a marginal direct
  link. The route that delivered now sticks until a delivery on it fails.
  A direct copy at -60 dBm or stronger drops a relay preference.
- **Fixed:** deliveries reported the path of the first reply copy, so a
  module sent through a relay often showed "Direct". They now report the
  route the bridge chose.
- **Fixed:** a module heard directly at -81 to -88 dBm was sent DIRECT,
  which stops relays forwarding it. "Strong enough for direct" is now
  -80 dBm, and a weak link with no known relay floods.
- **Changed:** retries go to a flood after one failed attempt (was: the
  same route twice, then a flood).
- **Added:** `MESH_BENCH_DEAF_TO`, a bench-only setting that makes a node
  ignore copies sent straight from listed nodes, to force a relay on a
  desk.
- Checked on the tablet: 73 of 73 deliveries to the far dummy went through
  the field module on the first attempt.

### Bridge — `072b095`
- **Added:** SAFE/ARM switch on P0.06, four WS2812B status pixels on P0.08
  and LED power on P0.13, on the COM12 bench bridge.
- **Fixed:** LEDs all white: 400 ns T0H read as all ones on these
  WS2812Bs; 250 ns decodes. Colour order is GRB.
- **Fixed:** LEDs dark: the power pin is now driven high, and only when the
  LED settings are valid.

### Dummy 0.2.0 — `072b095`
- **Added:** simulated resistance scans (0x24) and output status (0x27), so
  the app's automatic continuity checks no longer mark dummies red.

### App — `cacc1ba`
- **Added:** experimental Mesh page: topology of how each module was last
  reached, with per-module route counts.
- **Added:** automatic continuity checks after discovery (one random module
  per heartbeat while not armed), with a reply LED that flashes green and
  stays red on no reply. Can be turned off in Settings.
- **Added:** RSSI next to each module's battery.
- **Added:** Settings tab with per-firmware battery settings; script
  runner; new app icons.
- **Fixed:** the group number field in a module's Settings crashed
  (PageStorage key collision).
- **Changed:** modules start collapsed. Module diagnostics moved into each
  module's Settings and radio maintenance onto the Radio page. The Master
  controller tile is gone; its live controls are on Fire and Scripts.

### Tools — `072b095`
- **Fixed:** `install_usb_bridge.py --master` gave a bridge with no
  switch/LED settings none at all; it now takes them from the repository
  config.
- **Added:** `scripts/tests/test_mesh_routes.py` (route choice unit tests).

## 2026-11-07

### Mesh — `e5fac00`
- **Added:** mesh v1, built in phases (see `docs/mesh_radio_design.md`):
  - multi-node simulator running the real firmware (`scripts/sim/mesh_sim.py`);
  - shared `main/mesh.py`: mesh header, duplicate filter, flooding relays
    up to 2 hops, non-blocking send queue, new radio address;
  - RSSI-weighted relay wait with suppression, and an age field so relayed
    time code stays accurate;
  - reliable unicast: bridge route cache, `CMD_SEND_NFCP_RELIABLE` (0x1A),
    and a module reply cache so retries never run a command twice.
- **Fixed:** RSSI read the noise floor (about -100 dBm); it is now sampled
  during the packet.

### Bridge — `e5fac00`
- **Changed:** faster USB frame handling (command hold time 42.5 ms to
  12.5 ms).

### Field / Dummy — `e5fac00`
- **Fixed:** the LCD blocked the radio; it now writes only changed
  characters, one I2C transaction per row.

## 2026-11-06

### Field / Dummy — `a1d2523`
- **Changed:** radio link, script engine and LCD menu shared between the
  two firmwares (`module_link.py`, `module_script.py`, `module_lcd.py`).
- **Field 0.3.0:** arm voltage check, button polarity, short threshold.
- **Added:** `scripts/sim/firmware_trace_harness.py` to run either firmware on
  a PC.

## 2026-11-05

### Bridge 0.4.1, App — `e41f6d9`
- **Added:** Firmware tab: bridge updates over USB, field updates over RF.
- **Added:** a new bridge bundle boots on trial and is restored from `.bak`
  unless an app session confirms it.
- **Fixed:** Android's file picker renamed `.mpy` to `.bin`.

### Hardware — `9aff6dd`
- **Changed:** nice!nano filesystem grown to 512 KiB. Reflashing moves the
  filesystem, so module files must be reinstalled.

## 2026-11-04

### App, Bridge — `5f57fe0`
- **Added:** Android controller app and the nice!nano USB master bridge.
- **Fixed:** Android USB reads dropped replies on the Galaxy Tab A9+;
  they now use a queued UsbRequest.
- **Fixed:** expansion tiles read the page scroll offset as their state.
- **Fixed:** after a STOP and reconnect the RF keepalive stayed off until
  something was re-armed.

## Earlier history

Summarised from commit messages; see `git log` for details.

- **2026-10:** field resistance calibration and persistence (`912bb61`);
  master script upload and module card layout fixes (`7ad2a60`).
- **2026-09:** nRF support: channel scan, FICR unique IDs, persisted
  bridge channel, battery telemetry, no-ack script step firing, more
  stable script upload chunks, master bridge update flow, nRF discovery
  and LCD work. Legacy E77 and RP2040 code removed (`9e309dc`).
- **2026-06:** wireless E77 modules: known-good script upload, MC33996
  continuity fixes, unique-ACK arm flow, OTA updates, script fire timing.
- **2026-05:** NFCP upload tooling, wired RS485 version archived, first
  wireless E77 transport and network pairing.
