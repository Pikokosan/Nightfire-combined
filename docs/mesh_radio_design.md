# Mesh Radio Layer Design

Status: draft, branch `Mesh`. Nothing here is implemented yet.

## Goals

- **Self-healing.** A module that can't hear the bridge directly is reached
  through another module, with no manual route setup. When a module dies,
  moves or gets blocked, traffic finds another path on its own.
- **2 hops by default.** Bridge → relay module → target module. Frames
  never travel further than `MESH_MAX_HOPS`, which keeps latency bounded and
  floods small. The limit can be raised for testing.
- **Two traffic classes:**
  - **Fast broadcast**: fire, time code, script start/stop/heartbeat,
    keepalive. Lowest latency; delivered by quick repeats instead of acks.
  - **Reliable unicast** to a module's unique ID: arm/disarm, settings,
    info, continuity, script upload, OTA. Confirmed end to end and retried.
- **No changes to NFCP itself.** The command set, payloads and
  `nfcp.py` frames stay as they are. The mesh is a new header between the
  radio payload and the NFCP frame.

Out of scope for now:
- building support for a second bridge (the format allows for one; see
  Decisions);
- encryption or authentication (none today either);
- synchronized flooding (see Phase 4).

## What exists today

| Piece | Current behaviour |
|---|---|
| Radio (`nrf52840/native/nrf_radio/nrf_radio.c`) | nRF proprietary 1 Mbit, 252-byte max payload, whitening, hardware CRC, `last_rssi()`, CCA in `send()`. Base address `0x67676767`. |
| Radio payload | `network_id(2) \| seq(1) \| NFCP frame`. The seq byte is detected by a heuristic (`payload[2] != 0xFF and payload[3] == 0xFF`). |
| Duplicates (`module_link.py`) | Kept for 1.5 s, 16 entries, keyed on `(seq, frame type, src, target, cmd, first 16 data bytes)`. |
| Module TX | Every frame is sent 3 times, 20 ms apart. |
| Bridge TX (`nrf_bridge_main.py`) | Sends once, using the app's USB seq as the radio seq, then listens 100 ms for replies. |
| App retries (`master_controller.dart`) | Read-only queries and DISARM are retried. ARM, FIRE, START and STEP are never retried, because a lost ack can't be told apart from a lost command. |
| Master-link failsafe | A module disarms itself after 15 s without hearing any matching frame. |

What blocks a mesh:

- The seq is 8 bits and per sender, and the duplicate key has no
  originator. The master's NFCP `src` byte is always `0x01`, so two senders
  can collide.
- There is no hop count, so a relayed frame can't be told from an original
  one and could be relayed forever.
- Relays would have to edit the frame, which would break the
  signature-based duplicate key.

## Roles

- **Bridge**: the root. It originates all downlink traffic and is the sink
  for all uplink traffic. Its mesh ID is its `nrf_radio.device_id32()`.
- **Field and dummy modules**: endpoints, and every one is also a relay.
  Each module decides on its own, frame by frame, whether to forward (see
  Class F). `MESH_RELAY_ENABLED` exists only for bench tests.
- A relay forwards frames whatever its own arm state, group or unique ID.
  Only the final target checks whether it is armed.

## Frame format (mesh v1)

```
network_id  2   unchanged; checked first, as today
ctl         1   see below
origin      4   unique ID of the original sender (bridge or module)
msg_id      2   per-origin counter, never 0
relay       4   only when ctl.RELAY is set
age         2   only when ctl.TIMED is set; 100 µs units
NFCP frame  …   unchanged (FF SOF2 LEN payload CRC)
```

`ctl` bits:

| Bits | Name | Meaning |
|---|---|---|
| 7–6 | `VER` | Mesh version, `01`. |
| 5–4 | `MODE` | `00` flood: anyone may relay. `01` direct: nobody relays. `10` via: only the node named in `relay` forwards. |
| 3 | `RELAY` | The `relay` field is present. |
| 2 | `TIMED` | The `age` field is present (time-code frames). |
| 1–0 | `HOPS` | Transmissions so far, minus one. The originator sends `0`; each relay adds one. |

Header overhead is 9 bytes, up from 3 today: 13 with `relay` and 15 with
both optional fields. The largest current frame, an OTA write, still fits
in 252 bytes.

### The `relay` field

- **Downlink with `MODE=via`**: the bridge names the relay that should
  forward the frame.
- **Any frame a relay forwards**: the relay writes its own unique ID into
  `relay` and sets `RELAY`. This is how the bridge learns routes: a reply
  that arrives with `HOPS=1` and `relay=X` means the module is reachable
  through X.

### Duplicate suppression

The key is `(origin, msg_id)`. Kept for 2 s, up to 32 entries.

- The key covers only fields relays never change. `HOPS`, `relay` and `age`
  can change in flight without breaking it.
- The originator's quick repeats reuse the same `msg_id`, so every copy,
  direct or relayed, is acted on at most once.
- The bridge starts `msg_id` from a random value at boot, so a reboot can't
  replay IDs still sitting in a module's duplicate window.

### Keeping old firmware apart

Mesh v1 uses a **new radio base address** (for example `0x6D657368`), so
pre-mesh firmware never receives mesh frames. Mixed networks are not
supported: every node on a show must run mesh firmware. Without this,
old firmware's stream parser would hunt for `0xFF`, find the NFCP frame
after the new header, and execute it with no duplicate protection.

## Class F: fast broadcast (flooding)

Used for:
- FIRE, group or unique;
- TIME_SYNC and SCRIPT_HEARTBEAT;
- SCRIPT_START and SCRIPT_STOP as broadcasts;
- KEEPALIVE;
- DISARM as a broadcast;
- DISCOVER.

The class decides how a frame travels. The NFCP addressing inside still
decides who acts on it.

**Originator** (normally the bridge)
- Sends the frame 3 times with `MODE=flood`, `HOPS=0`, at 8–12 ms gaps
  with random jitter (today's gap is 20 ms).
- There is no ack.
- The bridge does these repeats itself, so the app sends one USB request.

**Every receiver**, on the first copy of a `(origin, msg_id)`:
1. If addressed to this node, act on it immediately: fire, sync, and so on.
2. If `HOPS < MESH_MAX_HOPS - 1` and relaying is enabled, schedule a relay.
   With 2 hops that means only `HOPS=0` copies are relayed.

**Relay timing and suppression** (managed flooding):
- **Wait** = `1 ms + (rssi + 95) × 6 / 55 ms + random(0–2 ms)`, with RSSI
  clamped to −95…−40 dBm.
  - A weak copy (−95 dBm) waits 1–3 ms.
  - A strong copy (−40 dBm) waits 7–9 ms.
  - Nodes at the edge of coverage, whose relay reaches new ground, go
    first. Nodes right next to the bridge go last.
- **Suppression:** count copies with `HOPS ≥ 1` of the same key heard
  during the wait. If 2 or more are heard, cancel the relay. Repeats from
  the originator (`HOPS=0`) don't count.
- **Sending:** transmit the frame twice, about 5 ms apart, with `HOPS + 1`,
  `relay = self`, and `age` updated if `TIMED` is set.

**Airtime:** a 30-byte FIRE frame is about 0.3 ms on air at 1 Mbit. Three
originator copies plus two relays sending twice each is about 7 frames, or
roughly 2 ms of air per fire command.

**Latency target**, to be measured:
- Under 5 ms to direct modules.
- Under 15 ms at 2 hops, with relays in MicroPython.

### Time code

Today, script cues fire on the master clock (`start_master_ms`, TIME_SYNC,
HEARTBEAT), so fire timing at a module depends on sync accuracy, not on
FIRE latency. Relayed sync frames need to correct for the time they spend
in relays:

- TIME_SYNC and SCRIPT_HEARTBEAT are sent with `TIMED` set and `age = 0`.
- **Each relay:**
  - Records `ticks_us()` when `recv()` returns.
  - Adds its own hold time just before sending:
    `age += (tx_us − rx_us) / 100 + MESH_HOP_LATENCY`.
- **Receiver:** `master_now = ticks + (age × 100 µs) + MESH_RX_LATENCY`.
- `MESH_HOP_LATENCY` and `MESH_RX_LATENCY` are calibrated on the bench
  (air time plus MicroPython receive overhead).
- Only the first copy is processed. If a direct (`HOPS=0`) TIME_SYNC
  arrives within 20 ms of a relayed one, the direct copy replaces the
  sample, since it has less error.
- Target: modules agree on master time to within ±2 ms at 2 hops.

## Class R: reliable unicast

Used for:
- ARM and DISARM to one module;
- SET_GROUP, SET_NETWORK, SET_RADIO_CHANNEL and SET_UNIQUE;
- INFO and PING;
- CONTINUITY, RESISTANCE, SET_RESISTANCE_CAL and OUTPUT_STATUS;
- SCRIPT_CLEAR, SCRIPT_LOAD and SCRIPT_STEP;
- SCRIPT_START to one module;
- OTA.

### Request and reply

- Each class R request gets exactly one reply from the target: the NFCP
  ACK, NACK or response it already sends. That reply is the end-to-end
  confirmation; there is no separate mesh ack.
- **The reply goes back the way the request came:**
  - A direct request gets a direct reply.
  - A request through X gets a reply addressed through X.
  - A flooded request gets a flooded reply.
  - Modules keep no routing state of their own.
- **Retries reuse the same `msg_id`.** The target keeps a small reply cache
  (4 entries, 3 s), keyed by `(origin, msg_id)`:
  - A duplicate of a finished request re-sends the cached reply without
    executing the command again.
  - A duplicate of a request still running (such as a resistance scan) is
    ignored.
- Because of this cache, ARM, SCRIPT_START and STEP become **safe to
  retry**, which today's app deliberately avoids.

### Bridge route cache and retries

The bridge keeps a route per module:
`uid → (relay or direct, rssi, last_heard_ms, failures)`.

- **Learning:** it is updated from every uplink frame the bridge hears:
  - discover replies;
  - command replies;
  - flooded frames a relay forwards with `relay` set.
- **Choosing a route** (revised 2026-11-08, see "Route preference" below):
  - The route that last worked, if any (it sticks until it fails).
  - Otherwise direct if the module was heard directly in the last 30 s at
    ≥ −80 dBm.
  - Otherwise through the relay it was last heard through.
  - Otherwise flood.

The bridge, not the app, runs the delivery loop. That keeps it close to
the radio timing:

1. Send on the cached route and wait for the reply. The app passes the
   timeout per command (resistance scans and OTA END are slow).
2. If no reply, drop the cached route and flood. Learn the new route from
   whichever path the reply comes back on.
3. If still no reply, flood once more.
4. If there's still no reply, report failure to the app.

This needs a new bridge USB command (`CMD_SEND_NFCP_RELIABLE`). It takes
the target uid, the timeout and the NFCP frame. It returns the reply frame
or a failure code, plus the route that worked.

### Discover

DISCOVER is a class F flood carrying the nonce, as today.

- Each module replies the way the first copy reached it: direct, or
  through the relay that forwarded it.
- Reply backoff widens from today's window to cover the relay delay. The
  value is to be tuned.
- One discover fills the bridge's whole route table.

## Failsafe and safety rules

- **Master link:** any frame whose `origin` is the bridge counts as hearing
  the master, whatever its `HOPS`. This means a module that only hears the
  bridge through a relay still gets keepalives and stays armed.
- **Relays never act** on frames that aren't addressed to them. Forwarding
  doesn't depend on whether the relay is armed.
- **Acting on a frame comes before relaying it**, so a relay's own fire
  is never delayed by its forwarding work. Check that firing a cue doesn't
  block the main loop long enough to miss a relay window.
- **No double firing:** FIRE executes at most once per `(origin, msg_id)`
  however many copies arrive.
- **Big transfers never flood** except as the last-resort retry. SCRIPT_LOAD
  and OTA writes are large, and flooding them would fill the channel.

## Implementation plan

**Phase 0: a PC mesh simulator.** Done: `scripts/mesh_sim.py` with the
scenarios in `scripts/mesh_scenarios.py`. See "Phase 0 baseline" below.

**Phase 1: frame format and plain flooding.** Built on branch `Mesh` and
checked in the simulator; **not yet run on hardware**. See "Phase 1
results" below.
- `main/mesh.py` (new) has:
  - the header encode/parse and relay copy;
  - the `(origin, msg_id)` duplicate cache (`MeshSeen`);
  - the non-blocking send queue (`MeshQueue`);
  - `MeshBridge`, the bridge side: it originates frames with 3 repeats
    8–12 ms apart, and drops duplicates, its own relayed frames and
    pre-mesh payloads before any USB work.
  - It imports nothing but `time`, so the bridges don't need `nfcp.py`.
- `main/module_link.py`:
  - **Receive:** the cheap mesh checks (network, header, own echo,
    duplicate) come first. Then a fast NFCP decode
    (`link_decode_nfcp`): it unescapes in one call and doesn't check the
    Python CRC, since the radio's hardware CRC covers the payload.
  - **Relaying:** the module queues the relay copy, then acts on the frame.
    Every module relays each flooded frame once, as 2 copies after a
    random 1–6 ms wait. It doesn't relay unicast frames addressed to
    itself.
  - **Sending:**
    - `tx()` sends the first copy now and queues the repeats.
    - `service()` sends queued items. Each makes one clear-channel attempt;
      if the channel is busy, it's re-queued after a random backoff instead
      of sleeping.
    - The discover backoff is a queued delay.
    - `flush()` sends everything at once before a channel change or reboot.
- `main/field_main.py` and `main/nrf_dummy_main.py`:
  - They call `link.service()` every loop.
  - The `recv()` timeout is the smallest of the next queued send, the next
    script cue (`ScriptEngine.wait_ms`) and 50 ms.
  - New settings: `MESH_MAX_HOPS`, `MESH_RELAY_ENABLED` and
    `MESH_RADIO_ADDRESS`.
- **Bridges:**
  - `nrf_bridge_main.py` (master UART bridge) and `nrf_usb_monitor_main.py`
    (the app's USB bridge) both use `MeshBridge`.
  - The USB bridge's keepalive and master stop/disarm code still build
    pre-mesh packets. They now send through `MeshBridge.send_legacy`, which
    converts them.
  - USB/UART polling dropped from 50 ms to 10 ms (UART bridge) and 20 ms
    (USB bridge).
  - The app and `master_main.py` are unchanged: the bridges still take and
    deliver plain NFCP frames.
- `mesh.py` is added to the field bundle, the USB bridge bundle, and the
  dummy, bridge and OTA upload/package scripts.
- **Phase 2 work already done here:**
  - the `recv()` timeout bounded by queued sends and cues;
  - removing the sleeps from repeats and clear-channel backoffs.
  The simulator showed relays blocking in CCA backoff and making cues
  60–80 ms late, so these couldn't wait.

**Phase 2: suppression and timing.** Done on branch `Mesh`, checked in the
simulator and on hardware. See "Phase 2 results" below.
- **Time code** (`mesh.py`, `module_link.py`, `module_script.py`):
  - TIME_SYNC and SCRIPT_START frames carry the `age` field.
  - Each sender rewrites it at the moment of sending, from a stamp. The
    stamp holds when the frame arrived (`ticks_us`), its age then, and the
    transit time of the hop (CCA, ramp-up, airtime and the receiver's
    anchoring delay).
  - Bridges age from when the frame arrived over USB/UART, so their own
    handling time counts.
  - Relays add their wait and queue time.
  - The receiver records the arrival time of each packet (`rx_us`).
    `time_sync()` uses master time + age at that arrival, not when Python
    got round to handling it. The fallback start (no master start time)
    works the same way.
- **Relays:**
  - The wait before relaying comes from RSSI: 1–7 ms, plus 0–2 ms random;
    weak signal relays first.
  - Pending relays sit in `link.relays`. A relayed copy heard during the
    wait counts toward suppression (2 copies cancels the relay).
  - Replies to the bridge are never suppressed (`MESH_SUPPRESS_RESPONSES`).
- **Repeats:** a module's repeat gaps are now jittered ±25%. With fixed
  35 ms gaps, two modules whose first discover replies collided collided on
  every repeat too; the simulator found this as discover failures on far
  modules.
- **Not done:** "prefer a direct TIME_SYNC over a relayed one". With
  age-corrected relayed samples the spread already meets the goal, so it
  isn't needed.

**Phase 3: reliable class.** Done on branch `Mesh`, checked in the
simulator, on hardware and in the app on the emulator. See "Phase 3
results" below.
- **Bridges** (`MeshBridge` in `mesh.py`):
  - **Route cache:** learned from every frame heard, duplicates included.
    Direct if heard directly within 30 s at -88 dBm or better; otherwise
    through the relay it was last heard through; otherwise flood. (Changed
    on 2026-11-08: see "Route preference".)
  - **New command** `CMD_SEND_NFCP_RELIABLE` (0x1A): uid(4) timeout_ms(2)
    NFCP frame.
  - **Delivery:** the cached route twice, then a flood (now: once, then
    two floods). Each attempt waits
    timeout/3 (at least 250 ms) and reuses the same `msg_id`. A route that
    gets no reply is forgotten.
  - **Answer:** status, route, relay uid and attempts, sent once the reply
    has arrived (the reply itself is forwarded as a normal `CMD_NFCP_RX`)
    or all attempts have failed.
  - `CMD_NFCP_RX`'s third byte is now the frame's hop count.
  - Version strings: `usb-master-0.4.2`, `usb-monitor-0.3.3`.
- **Modules** (`module_link.py`):
  - **Reply:** the first reply to a unicast request reuses the request's
    `msg_id` and goes back the way it came (direct, through the named
    relay, or flooded). It's kept in a reply cache (4 entries, 3 s).
  - **Retry:** a copy of a cached request arriving 150 ms or more later is
    a retry. The module re-sends the cached reply and doesn't run the
    command again, even after the 2 s duplicate window. Copies within
    150 ms are repeats or relays of the same attempt.
  - **Relaying a retry:** relays treat a 0-hop copy arriving 150 ms or
    more after the last as a new attempt, and decide again whether to
    forward it. The route may have changed from "via X" to a flood.
  - `MESH_MODE_DIRECT` frames are never relayed. `MESH_MODE_VIA` frames
    are relayed only by the named relay.
- **App:**
  - `RadioSession._queryModule` uses 0x1A when the bridge is a mesh build
    (`supportsReliableDelivery`); otherwise it sends plain `SEND_NFCP`.
  - The reply still arrives through the normal path, so the existing reply
    matching is unchanged.
  - A failed delivery fails the query at once ("No reply from module …
    (3 attempts)").
  - The module card shows "Route: Direct / Via 0x… / Flooded" and records
    the hop count.
  - **Kept:** the app's own retry loops (status, disarm, upload, firmware
    update). Each app retry is a new request that the bridge again delivers
    reliably, and those commands are safe to repeat.

**Phase 4: speed, only if measurements need it.**
- Move the relay fast path (receive → hold → re-send with updated `age`)
  into `nrf_radio.c`, timed by a hardware timer.
- Synchronized flooding, where relays transmit in the same microsecond and
  rely on capture, could then follow for time code.

### Files affected

| File | Change |
|---|---|
| File | Phase 1 (done) | Still to come |
|---|---|---|
| `main/mesh.py` (new) | Header, duplicate cache, send queue, `MeshBridge`. | Suppression, age (2); reply cache (3). |
| `main/module_link.py` | Mesh receive/relay, fast NFCP decode, non-blocking sends. | |
| `main/module_script.py` | `wait_ms()` for the next cue. | Age-corrected time sync (2). |
| `main/field_main.py`, `main/nrf_dummy_main.py` | Service the queue; bounded `recv()`; mesh settings. | |
| `main/nrf_bridge_main.py`, `main/nrf_usb_monitor_main.py` | `MeshBridge`, faster polling. | Route cache, reliable send (3). |
| `main/bridge_radio.py` | | New USB command and fields (3). |
| Field and USB bundlers, dummy/bridge/OTA scripts | Include `mesh.py`. | |
| `scripts/firmware_trace_harness.py` | Delivers mesh frames. | |
| `scripts/mesh_sim.py`, `mesh_scenarios.py` | Decode mesh frames; `deep3` scenario. | |
| `apps/nightfire_controller` | Unchanged. | Use reliable send; show hops and route (3). |
| `main/PC_multi_module_emulator.py`, bench scripts that use the radio directly (`scripts/test_nrf_*.py`, `scripts/test_master_bridge_*`, `scripts/test_nrf_to_master_bridge_raw.py`, `scripts/diagnostics/read_nrf_radio_stats.py`) | **Not updated: still pre-mesh.** | Update before using them against mesh firmware. |
| `main/master_main.py` | Unchanged (it talks to the UART bridge). | |

## Phase 0 baseline

### The simulator

`scripts/mesh_sim.py` runs the real, unmodified firmware on several
simulated nodes:
- a bridge running `nrf_bridge_main.py`;
- dummy modules running `nrf_dummy_main.py`;
- field modules running `field_main.py`.

All nodes share one virtual clock and one simulated radio channel.

```
py -3 scripts/mesh_sim.py --list
py -3 scripts/mesh_sim.py --scenario chain --out chain.trace
py -3 scripts/mesh_sim.py --scenario all --seed 3
```

What it models:
- **Isolation:** each node has its own copy of every firmware module, its
  own fake hardware, its own in-memory flash (config saves survive a
  reboot) and its own clock with offset and drift.
- **The app:** the scenario script plays the app by writing bridge USB
  frames into the bridge's fake UART.
- **The radio:**
  - airtime at 1 Mbit;
  - path loss from node positions;
  - obstacles and fixed per-pair links;
  - per-packet fading;
  - collisions with capture;
  - clear-channel assessment;
  - half-duplex radios;
  - the C driver's whole-millisecond receive timeout;
  - a node hears a packet only if it was already in `recv()` when the
    packet started.
- **CPU time:** each node is charged for the calls its firmware makes,
  plus radio-driver costs and garbage-collection pauses. Only code the node
  itself loaded counts: the simulator's own trace decoding used to be
  charged to nodes, and that was fixed during Phase 1.
  A busy node is deaf. The costs are calibrated against hardware (below);
  `--cpu-scale` scales the whole model.

### CPU calibration (2026-11-06)

`scripts/diagnostics/mesh_cpu_benchmark.py` ran the real `nfcp.py`,
`module_link.py` and `bridge_radio.py` receive paths on the promicro field
module (nice!nano nRF52840, MicroPython 1.29 preview).
- The code was compiled in RAM over the raw REPL. Nothing was written to
  flash.
- The module was soft-reset afterwards, and its firmware came back with
  "Startup status: OK".
- The PC counted the same paths with the simulator's rule.

Two runs: the pre-mesh receive path, and the mesh v1 receive path (after
Phase 1).

| Workload | Pre-mesh | Mesh v1 | Calls (pre / mesh) |
|---|---|---|---|
| 116-byte reply for another node | 43.2 ms | 3.75 ms | 437 / 29 |
| 14-byte request for another node | 4.5 ms | 2.95 ms | 54 / 31 |
| 14-byte request for this node (new) | 4.8 ms | 3.20 ms | 64 / 24 |
| Script heartbeat broadcast | 6.6 ms | 3.01 ms | 69 / 29 |
| Group fire | 4.7 ms | 3.13 ms | 60 / 32 |
| Duplicate request | 4.8 ms | 0.70 ms | 61 / 8 |
| Bridge USB frame parse (unchanged code) | 7.1 ms | 9.0 ms | 81 / 81 |
| Empty function call | 14.5 µs | 13.7 µs | 1 / 1 |
| `gc.collect()` | 41–49 ms | 42–49 ms | |

- **Model: a flat 92 µs per call** (Python or C), which fits all 14
  workloads within about 30%.
  - The unchanged bridge workload alone moved 27% between runs, so that's
    about the measurement's noise floor.
  - Adding a per-line or per-bytecode term didn't help: the pre-mesh code is
    loop-heavy and the mesh code allocation-heavy, and no single
    per-line cost fits both.
  - The first run's fit (46 µs per call plus 5.3 µs per line) only fitted
    the pre-mesh code.
- **GC:** a long frame allocates about 930 bytes pre-mesh and 190 bytes
  with mesh, with about 110 KB of heap free. The model pauses for 45 ms
  roughly every 25,000 calls.
- **The NFCP CRC dominates:** it's computed bit by bit in Python and is 64%
  of the executed lines for the 116-byte frame and 48% for the 14-byte
  one. The radio's hardware CRC already protects every frame, so checking
  it again in Python is the cheapest large speed-up available.

Re-run the benchmark after changing the receive path or the MicroPython
build:

```
py -3 scripts/diagnostics/mesh_cpu_benchmark.py --port COM16
```

**Output:**
- **Pass/fail checks against the mesh goals:** discover, info, arm, fire
  latency, group fire, script upload, and how closely modules fire the
  same scripted cue.
- **Per-node radio statistics:** frames sent and received, collisions,
  frames missed while busy, CCA backoffs and airtime.
- **A trace (`--out`):** every transmission, every missed frame with the
  reason, and every firmware print.

**Scenarios:**

| Scenario | Topology |
|---|---|
| `star` | 4 modules, all in direct range. Checks the simulator itself. |
| `chain` | Bridge, A at 70 m, B at 140 m. B needs one relay. |
| `field` | 8 modules: 4 near, 3 two hops out, 1 behind an obstacle. |
| `heal` | Two possible relays for B; one dies mid-show. |
| `deep` | A 3-hop chain. With the default `MESH_MAX_HOPS` = 2, C must stay out of reach. |
| `deep3` | The same chain with `MESH_MAX_HOPS` = 3, so C is reachable. |
| `crowd` | 12 modules in range, for contention. |
| `field_fw` | Field firmware alongside a dummy. |

### What it shows for the current (pre-mesh) firmware

Seeds 1–3, calibrated model. These were re-run against the last pre-mesh
commit with the corrected simulator; the Phase 1 results table has the
final pre-mesh numbers.

1. **Coverage.** Modules beyond about 100 m, or behind an obstacle, are
   never discovered (`chain` B, `field` f1–f3 and w1, `deep` B and C).
   This is the gap the mesh has to close. Every check on those modules
   fails today.
2. **Processing time, not airtime, sets latency.**
   - From the bridge starting to send to a module firing takes about 7 ms.
     The frame is on air for about 0.2 ms; the rest is MicroPython.
   - A relay in Python would add at least another 5–7 ms per hop, plus its
     wait before relaying. That makes the 15 ms two-hop target tight unless
     the receive path gets cheaper.
3. **The bridge holds app commands for up to 50 ms.**
   - It only checks the USB link between 50 ms `recv()` calls, so app → air
     varied from 17 to 64 ms.
   - Time code suffers too. The app stamps TIME_SYNC when it sends it, so
     every module runs the show 70–80 ms behind the app's clock. They agree
     with each other (spread 1–3 ms), but not with the app.
4. **The bridge saturates when many modules reply at once.**
   - In `crowd`, one discover missed modules in most runs: 7 of 12 in the
     first runs, 5 of 9 checks passed across seeds after the correction.
   - Each frame costs the bridge several milliseconds in Python (dedupe,
     USB framing, CRC). During the burst of discover replies it was busy
     for 44 of them and missed them.
   - Mesh relaying adds traffic, so this limit matters.
5. **Some commands are lost in range.**
   - It happens rarely with the calibrated model: 0–1 lost per run in
     `star` and `field_fw`.
   - The pattern is the bridge sending the next command right after a frame
     ends, while modules are still processing that frame or sleeping
     between their own reply repeats.
6. **Scripted cues fire late.** A cue fires on the first loop pass after
   it's due, and a pass can block for up to 50 ms in `recv()`.

What this means for the mesh design:
- Skip the Python NFCP CRC on radio frames, since the radio hardware CRC
  already checks them, or move it into C. That is about half to
  two-thirds of today's receive cost.
- Drop frames that aren't for this node using the fixed mesh header, with
  a handful of Python calls, before running the NFCP stream parser.
- At the bridge, drop duplicates and frames that don't need forwarding
  before doing any USB framing.
- Never sleep between repeats. Repeats and relays go through a
  non-blocking queue serviced by the main loop, so the node keeps
  listening.
- The originator repeats every class F frame. Class R requests are retried
  with the same `msg_id`.
- The bridge waits a few milliseconds after a frame ends before sending,
  and checks the USB link more often than every 50 ms.
- The bridge stamps time code itself, as close as it can to sending it.
- The main loop's `recv()` timeout is never longer than the time to the
  next due cue, relay or repeat.

## Phase 1 results (simulator, 2026-11-06)

The same 8 scenarios over seeds 1–3, with the recalibrated CPU model. The
pre-mesh column is the last pre-mesh commit
(`--src <checkout of HEAD>/main`).

| | Pre-mesh | Mesh v1 |
|---|---|---|
| Checks passed, all scenarios | 305 / 570 (54%) | 515 / 570 (90%) |
| Failures other than cue timing | 222 | 12 |
| `chain` (B needs a relay) | 18 / 45 | 38 / 45 |
| `field` (3 far + 1 behind an obstacle) | 69 / 153 | 145 / 153 |
| `heal` (relay dies mid-show) | 54 / 108 | 95 / 108 |
| `deep3` (3 hops, `MESH_MAX_HOPS` = 3) | 18 / 63 | 54 / 63 |
| `star` (all in range) | 67 / 81 | 76 / 81 |
| `crowd` (12 in range) | 5 / 9 | 8 / 9 |
| Fire, 1 hop (bridge starts sending → module fires) | median 7.1 ms | median 3.1 ms, max 15 ms |
| Fire, 2 hops | not reachable | median 10.0 ms, max 15.5 ms |
| Fire, 3 hops (`deep3`) | not reachable | median 20 ms |
| Spread of one scripted cue across modules | 2.9 ms median (in-range modules only) | 8.9 ms median, 21 ms max |

What's left, and where it's handled:
- **Cue spread is now the main gap**, against a 5 ms goal. The modules
  sync to TIME_SYNC and heartbeats, which now often arrive relayed, and a
  relay adds 5–20 ms with no correction. Phase 2's `age` field and
  preferring direct sync samples are aimed at exactly this.
- **Congestion from plain flooding:**
  - every module relays everything, so in-range frames sometimes collide;
  - fire occasionally arrives on a repeat (15–22 ms);
  - discover finds 10–11 of 12 in `crowd`.
  Phase 2's suppression targets this.
- **`deep`:** C stays out of reach with the default 2 hops, as intended.
- **Compatibility:** every node must run mesh firmware, because the radio
  address changed. The bench scripts listed under "Files affected" still
  speak the pre-mesh format.

## Phase 3 results (2026-11-07)

**Simulator:** 6 seeds × 8 scenarios, Phase 3 firmware. The app's
requests went through the bridge's reliable delivery, or through plain
`SEND_NFCP` (`--plain`) for comparison:

| | Plain send | Reliable delivery |
|---|---|---|
| Checks passed | 1082 / 1140 | **1118 / 1140** |
| Failures other than cue timing | 46 | **11** |
| Info / arm / disarm / script-upload failures | 25 | **1** |
| What's left | | discover (broadcast, not covered) 9, one fire, one upload |
| Cue spread (median / max) | 1.4 / 13.4 ms | 1.7 / 7.1 ms |
| 2-hop fire (median) | 9.7 ms | 9.5 ms |

- **Routes:** in `chain`, A was reached directly and B through A, both on
  the first attempt.
- **No double execution:** across 6 seeds of five scenarios, there were 26
  retried deliveries and 20 replies answered from the reply cache. Every
  module ran SCRIPT CLEAR, SCRIPT LOAD, ARM and DISARM exactly as many
  times as it was asked to.

**Hardware** (USB bridge `usb-master-0.4.2` on COM12, field module on
COM16):
- **Smoke test:** 11 of 11 checks.
  - **Reliable info:** delivered direct on the first attempt, 132 ms.
  - **Reliable pings:** 50 of 50.
  - **Timing:** the module's reply reaches the PC in 83 ms median, against
    about 73 ms for a plain ping; the delivery answer follows 4 ms after
    the reply.
- **App on the Android emulator**, through `emulator_serial_relay.py` to
  the same bridge:
  - connect, discover and the module info read all worked through 0x1A;
  - the module card shows "Route: Direct" and RSSI -49 dBm;
  - a continuity check (24 channels, 1–2 s on the module, longer than one
    attempt) completed.
- **App tests:** 160 pass, including 5 new ones in
  `test/reliable_delivery_test.dart`. Analyze is clean.

**Known limits:**
- Discover is a broadcast, so it isn't delivered reliably. The app already
  repeats discovery rounds.
- A module's own (non-reply) frame could share a `msg_id` with a pending
  delivery to it and be taken for the reply. The chance is about
  1 in 65,536 per frame during a delivery.
- Each bridge install appends another `MASTER_ENABLED = True` to its
  config. This is an existing installer quirk and harmless.

## Route preference (2026-11-08)

Tested with the bridge on the Android tablet, the field module D96E30DD as
the relay and the dummy 62A75130 further away.

**Found:**
- With the field module next to the bridge, the dummy (-81 to -83 dBm)
  was sent DIRECT, which forbids relaying, and lost replies. A relay next
  to the bridge can't help anyway: it is as far from the dummy as the
  bridge.
- With the field module halfway, the dummy flipped between the relay and
  direct. Every direct copy the bridge heard refreshed the direct route,
  and copies near -80 dBm passed the "strong" test. The bridge also
  reported the route of whichever reply copy arrived first; a VIA target's
  own transmission is often heard directly before the relay forwards it.

**Now** (`MeshBridge` in `mesh.py`, tests in `scripts/test_mesh_routes.py`):
- `MESH_ROUTE_DIRECT_DBM` is -80. A weak direct link with no known relay
  floods instead of going DIRECT, so any module can carry it.
- **Preferred route:** a route that delivered sticks until a delivery on it
  fails. A relay becomes preferred when it carries a reply while the
  direct link is weak or silent. Weaker or stale direct copies don't
  change it; a direct copy at `MESH_ROUTE_CLOSE_DBM` (-60) or stronger
  drops a relay preference.
- **Retries:** the chosen route once, then floods.
- **Reported route:** the route the bridge chose. A flood still reports the
  path of the first reply copy.

**Result on the tablet:** 73 of 73 deliveries to the dummy went through
D96E30DD on the first attempt (none direct, flooded or failed). The app's
experimental Mesh page shows the topology.

**Bench setting:** `MESH_BENCH_DEAF_TO` (bridge or node config, a tuple of
uids) makes a node ignore copies sent straight from those nodes, to force
a relay on a desk. Leave it out of show configs.

**Updating the bridge from the tablet:** an update stays on trial until an
app session confirms it. If the app can't reconnect and the dongle loses
power first, it rolls back to the previous bundle without saying so.

## Phase 2 results (2026-11-06)

**Simulator** (all scenarios, calibrated model). These are seeds 1–3, the
same as the Phase 1 table, unless noted:

| | Phase 1 | Phase 2 |
|---|---|---|
| Spread of one scripted cue across modules | 8.9 ms median, 21 ms max | **1.5 ms median, 3.3 ms p90, 5.6 ms max** (6 seeds) |
| Checks passed (seeds 1–3) | 515 / 570 | 547 / 570 |
| `chain` | 38 / 45 | 44 / 45 |
| Fire, 2 hops | 10.0 ms median | 9.1 ms median, 16 ms max (6 seeds) |
| Fire, 3 hops (`deep3`) | 20 ms median | 15 ms median |
| Failures other than cue timing | about 2% | about 3% (34 / 1140 over 6 seeds, excluding 3-hop fire) |

The remaining non-cue failures are single-shot requests or replies where
all 3 copies were lost: discover, info, upload and disarm, mostly to far
modules. That's the Phase 3 job (end-to-end retries).

**Suppression doesn't fire at MicroPython speed.** The counters showed 0
suppressions across all 20 modules in `field` and `crowd`. A module needs
about 3 ms just to process the frame it would relay, so with 1–9 ms waits
its wait is over before it can hear anyone else's relay.

Over 6 seeds:

| Variant | Effect |
|---|---|
| Suppression off | Identical results. |
| Waits 3–14 ms | No airtime saved (8.35 s vs 8.23 s); 2-hop fire 11.3 ms median. |
| Waits 4–18 ms | No airtime saved; 2-hop fire 13.1 ms; slightly more failures. |
| No RSSI weighting (random 1–6 ms) | Same within noise. |

So the short waits stay. The suppression code stays too: it costs nothing,
and it becomes useful with a faster relay path (Phase 4) or denser meshes.

**Hardware** (USB bridge COM12 and field module COM16, both reflashed):
- **Smoke test:** 9 of 9 checks, 100 of 100 pings.
- **TIME_SYNC through the bridge:** the module received each one direct
  with an age of 42.4–43.1 ms (one at 25.3 ms), and the age went into its
  clock offset.

**What that exposed: the USB bridge held every app command for about
42 ms** between reading it from USB and transmitting it. Fixed the same
day. The TIME_SYNC age measures the hold directly, so each step was
checked on the bridge:

| Change | Bridge hold (TIME_SYNC age) |
|---|---|
| Before | 42.5 ms |
| `UsbHexTransport.read`: byte reads, every waiting character in one pass (no 64-character cap), and the main loop takes up to 512 bytes per pass | 21.7 ms |
| CRC16 by 256-entry table in `bridge_radio.py` and `nfcp.py` (same results as the bit loop) | 16.6 ms |
| `BridgeFrameParser.feed_bytes()`: frames found by slicing, not one call per byte | **12.5 ms** |

- **Reader timings on the bridge** for a 48-character command line: the
  old reader took 30 ms, the new one 5.6 ms. A bulk read would take
  0.4 ms, but it waits until all the bytes it asked for have arrived. If
  USB dropped mid-line, the bridge would freeze there and couldn't send
  its stop/disarm broadcasts. The new reader never waits.
- **Hardware smoke test afterwards:**

  | | Before | After |
  |---|---|---|
  | Ping round trip, PC → module → PC (median) | 133 ms | **72 ms** |
  | Output status | 120 ms | 60 ms |
  | Info | 174 ms | 113 ms |
  | Resistance scan | 1.88 s | 1.00 s |

  The modules also build their replies with the table CRC, which accounts
  for part of this.
- **Tests:**
  - `scripts/test_usb_bridge_transport.py`: byte and text stdin, long
    lines, partial lines, and random streams against a reference decoder.
  - `scripts/test_bridge_frame_parser.py`: `feed_bytes()` returns the same
    frames as `feed()`.
  - The CRC table was checked against the bit loop on 2,000 random inputs.
  - `master_main.py` and other `feed()` users are unchanged.
- **What's left of the 12.5 ms:** about 5.6 ms is the character loop. The
  rest is the master's permit check and NFCP unescaping in Python.

**Test tooling:** the trace harness's fake radio gained `last_rssi()`,
which the relay code now calls.

## Phase 1 hardware test (2026-11-06)

**Setup:**
- The USB bridge (COM12) got the compiled master bundle with mesh:
  `install_usb_bridge.py --master --compiled`, an 18,205-byte bundle. The
  old bundle is saved in `dist/bridge_backup_20261106_221127`.
- The promicro field module (COM16) got the mesh field image (43,612 bytes)
  with its existing config. The old files are saved in
  `dist/field_backup_20261106_221021`.
- Both boot normally. The bridge reports radio address `0x6D657368`.

**Test:** `scripts/diagnostics/mesh_hw_smoke.py --bridge COM12
--module-port COM16 --repeat 50` plays the app over USB: host heartbeat,
then NFCP frames, with no ARM and no FIRE.

| Check | Result |
|---|---|
| Discover | Found D96E30DD; one copy forwarded to USB despite the 3 radio repeats. |
| Info | OK (field-module 0.3.0). |
| Output status | OK, 121 ms round trip. |
| Resistance scan | OK, 1.38 s, all 24 channels. |
| Script clear + load | Both ACKed; the module printed `SCRIPT CLEAR` / `SCRIPT LOAD ... total=3` once each. |
| Disarm | ACKed; the module printed `DISARMED` once. |
| 50 pings | 0 lost. Round trip PC → bridge → module → bridge → PC: median 134 ms, range 122–201 ms. This includes Windows USB-serial latency. |

These were measured on the module with counters loaded in RAM:
- Every request it receives is handled exactly once. Repeats show up only
  as duplicates.
- Handling a ping, including building and sending the reply, takes a
  median of 9.0 ms and at most 11.4 ms.

**Found while testing** (none caused by the mesh, all to fix):
1. **RSSI was wrong in the radio driver. Fixed the same day.**
   - **The bug:**
     - `nrf_radio.recv()` started the RSSI sample as soon as the receiver
       was ready, before any packet arrived, so `last_rssi()` returned the
       noise floor. The bridge reported -99 to -100 dBm for a module on the
       same desk.
     - CCA and `noise_sample()` also overwrote `last_rssi`.
   - **Fix in `nrf_radio.c`:**
     - while receiving, the radio's own shortcuts (`ADDRESS_RSSISTART`,
       `DISABLED_RSSISTOP`) sample RSSI during the packet;
     - `last_rssi()` now reports only received packets.
   - **Build:** MicroPython `be9a819` from
     `NightfirePY/e77-micropython` (the build script now finds that
     checkout), flashed by UF2 to both boards.
   - **Result:** the module sees the bridge at -42 to -51 dBm (median -44)
     over 289 packets, and the bridge reports -46 to -47 dBm for the module.
     The old driver read about -101 on the same bench.
   - **The bridge's files were wiped by the upgrade.** It was on a
     2026-09-16 MicroPython build with a different filesystem layout. It
     was reinstalled with `install_usb_bridge.py --master --compiled`, with
     the same settings as before (its config matched
     `nrf_bridge_config_usb.py`). The field module was already on the
     2026-11-05 layout and kept its files. **Back up any board on an older
     build before flashing it.**
   - **A/B test for reception** (150 pings every 100 ms, module counting
     what it handled):
     - old driver: 148 and 147 of 150 replies arrived, all 150 requests
       handled;
     - new driver: 146 of 150, 149 requests handled.
     So the RSSI fix doesn't affect reception. Under that load, Phase 1
     loses about 1–3% of single-shot requests or replies (all 3 copies
     missed). At 4 per second, 270 pings lost 2. Phase 3's end-to-end
     retries are aimed at this.
2. **The field LCD blocked the radio. Fixed the same day.**
   - **Measured on the module at idle, before the fix:**
     - the home screen redrew on almost every 350 ms refresh (36 times in
       15 s), because ADC noise changed the battery text each time;
     - each redraw took 66 ms on average and up to 90 ms, with 165 separate
       I2C transactions;
     - `lcd.service()` stalled the loop for up to 130 ms, so the module was
       deaf about 18% of the time.
   - **Fix:**
     - **`lcd1602_i2c.py`:** `show_lines()` writes only the changed span of
       each row, as a single I2C transaction, and `max_rows=1` lets a caller
       spread a redraw over loop passes. The call signature is unchanged.
     - **`module_lcd.py`:** draws at most one row per main-loop pass, and
       reads the battery for the home screen every 2 s instead of every
       refresh.
   - **After:**
     - 8 small partial writes in 15 s;
     - a full row takes 13.7 ms and a changed digit about 7 ms;
     - `lcd.service()` stalls for at most 27 ms (2.1 ms on average), so the
       module is deaf about 3.5% of the time at idle.
     The smoke test passes again: 9 of 9 checks, 50 of 50 pings.
   - **Tests:** `scripts/test_lcd1602_driver.py` checks the byte stream on an
     emulated PCF8574 + HD44780 for both pin mappings. The same emulator
     reproduces the old driver's screen exactly.
   - **Not checked:** what the physical LCD looks like. Have a look at the
     module's screen.
   - **Further options if needed:** I2C at 400 kHz (`LCD_I2C_FREQ`, if the
     backpack is happy), or a lookup table for character encoding.
3. **Battery read 0 mV** in INFO during the first test, but 8,260 mV in a
   later run, so a battery was probably just reconnected. Not
   mesh-related.
4. **Test-harness pitfall, already fixed in `mesh_hw_smoke.py`:** timestamp
   a request before writing it to USB. A reply can arrive before the PC
   thread sees the bridge's ack.

**Not yet tested on hardware:**
- relaying (there's only one module, so no 2-hop path);
- the tablet app against the mesh bridge (its USB protocol is unchanged);
- arming and firing (left to you).

**Rolling back:** upload the saved files from the two `dist/*_backup_*`
folders with `scripts/raw_micropython_upload.py`, or install from a
pre-mesh checkout with the same upload scripts.

## Decisions

Settled 2026-11-06:

1. **Hop limit.** The default is 2 hops: bridge → one relay → module.
   `MESH_MAX_HOPS` is a setting on the bridge and in node config so larger
   meshes can be tested. The 2-bit `HOPS` field allows up to 4 hops; going
   further would need a wider field.
2. **Who relays.** Every module is a relay. Each module decides per frame
   whether to forward, using the RSSI-weighted wait and suppression rules,
   so there's no manual relay assignment. `MESH_RELAY_ENABLED` exists only
   for bench tests and defaults to on.
3. **Manual fire.** It stays class F (fast, no acknowledgement). Nothing is
   sent back to the app.
4. **A second bridge.** Plan for it, but don't build it yet:
   - Duplicate keys already include the originator.
   - Replies go back to the request's `origin`.
   - Route caches live in each bridge.
   - Nothing in v1 may assume there's only one bridge (for example, a
     module must not hard-code the bridge's ID).
