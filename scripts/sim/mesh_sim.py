"""Multi-node radio simulator for the Nightfire mesh work (mesh design Phase 0).

The real firmware runs, unmodified, on several simulated nodes:
- a bridge running `nrf_bridge_main.py`;
- field modules running `field_main.py`;
- dummy modules running `nrf_dummy_main.py`.

All nodes share one simulated radio channel and run on a single virtual
clock, so a 60 s show replays in seconds and gives the same result every
time for a given seed. A scenario script plays the app: it writes bridge
USB frames into the bridge's UART and reads what comes back.

Each node gets its own copy of every firmware module, its own fake
hardware and its own in-memory flash, so module globals never leak between
nodes.

What the radio models:
- Airtime of the nRF proprietary 1 Mbit frame.
- Path loss from node positions (log-distance), with per-pair overrides,
  obstacles and per-packet fading.
- A receiver hears a packet only if it was already in `recv()` when the
  packet started. Packets that end in collision come back from `recv()` as
  CRC errors, like the real driver. The stronger packet survives if it is
  `capture_db` above the rest.
- Clear-channel assessment, half-duplex radios, channel and address
  filtering.
- MicroPython CPU time. Each node is charged for the calls and lines its
  firmware executes (counted with profiler/trace hooks, so the result is
  repeatable), plus fixed radio-driver costs and garbage-collection pauses.
  A node is deaf while it is busy. The costs are calibrated against a real
  nRF52840 module (see CpuModel); --cpu-scale scales them all.
- The C driver's receive timeout, which ends on whole ticks_ms of the
  node's own clock. Each node has its own clock offset and drift.

Usage:
    py -3 scripts/sim/mesh_sim.py --list
    py -3 scripts/sim/mesh_sim.py --scenario chain --out chain.trace
    py -3 scripts/sim/mesh_sim.py --scenario all

Scenarios live in scripts/sim/mesh_scenarios.py.
"""

from __future__ import print_function

import argparse
import builtins
import heapq
import io
import itertools
import math
import os
import random
import re
import sys
import threading
import traceback
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_DIR = os.path.join(ROOT, "main")
SIM_DIR = os.path.dirname(os.path.abspath(__file__))   # sim-only nodes
AUDIO_SIM_DIR = os.path.join(ROOT, "audio_box", "sim")   # simulated audio box (sim_audio_node.py)
if MAIN_DIR not in sys.path:
    sys.path.append(MAIN_DIR)

import bridge_radio  # noqa: E402  (host side of the bridge USB link)
import mesh  # noqa: E402
import nfcp  # noqa: E402

US_PER_MS = 1000
TICKS_PERIOD = 1 << 30
RAMP_US = 40              # nRF fast ramp-up
BYTE_US = 8               # 1 Mbit/s
FRAME_OVERHEAD = 1 + 4 + 1 + 2   # preamble, address, length, CRC


def airtime_us(length):
    return (FRAME_OVERHEAD + length) * BYTE_US


class NodeHalted(BaseException):
    """Stops a node's thread. Not an Exception, so firmware can't catch it."""


class NodeReboot(BaseException):
    """Raised by machine.reset(); the node restarts with its flash intact."""


# ---------------------------------------------------------------------------
# Scheduler and actors
# ---------------------------------------------------------------------------
class Scheduler(object):
    """Discrete-event clock. Exactly one actor thread runs at a time."""

    def __init__(self):
        self.now = 0
        self._heap = []
        self._seq = itertools.count()
        self.yielded = threading.Event()

    def at(self, t_us, fn):
        heapq.heappush(self._heap, (int(t_us), next(self._seq), fn))

    def run_until(self, t_end, stop=None):
        while self._heap and self._heap[0][0] <= t_end:
            if stop is not None and stop():
                return
            t, _, fn = heapq.heappop(self._heap)
            if t > self.now:
                self.now = t
            fn()
        if t_end > self.now:
            self.now = t_end


class Actor(object):
    """Runs `body` on its own thread, handing control back to the scheduler
    whenever it blocks on virtual time."""

    def __init__(self, sim, name, body, cpu=None):
        self.sim = sim
        self.sched = sim.sched
        self.name = name
        self.body = body
        self.cpu = cpu         # CpuModel; None for the scenario (app) actor
        self.calls = 0         # firmware Python and C calls made so far
        self.lines = 0         # firmware source lines executed so far
        self.charged_calls = 0 # work already turned into CPU time
        self.charged_lines = 0
        self.gc_countdown = cpu.next_gc(sim.rng) if cpu is not None else 0
        self._is_fw = {}
        self.go = threading.Event()
        self.token = 0
        self.thread = None
        self.finished = False
        self.halted = False
        self.debt = 0          # CPU time spent since the last blocking call
        self.state = "boot"    # what the node is doing while parked
        self.on_exit = None

    def start(self, t_us):
        self.arm(t_us)

    def arm(self, t_us):
        self.token += 1
        token = self.token
        self.sched.at(t_us, lambda: self._slice(token))

    def halt(self):
        self.halted = True
        self.token += 1

    def _slice(self, token):
        if token != self.token or self.finished or self.halted:
            return
        if self.thread is None:
            self.thread = threading.Thread(target=self._main, name=self.name)
            self.thread.daemon = True
            self.thread.start()
        else:
            self.go.set()
        self.sched.yielded.wait()
        self.sched.yielded.clear()

    # The node's CPU work is the Python and C calls its firmware makes plus
    # the firmware lines it executes, counted with profiler and trace hooks.
    # So a bridge parsing USB frames costs more than a module dropping a
    # packet, and a CRC loop costs what it does on the device. Counting is
    # deterministic, unlike wall-clock timing.
    def _fw(self, frame):
        name = frame.f_code.co_filename
        fw = self._is_fw.get(name)
        if fw is None:
            fw = name.startswith(FW_PREFIX)
            self._is_fw[name] = fw
        return fw

    def _profile(self, frame, event, arg):
        if (event == "call" or event == "c_call") and self._fw(frame):
            self.calls += 1

    def _trace(self, frame, event, arg):
        if event == "call" and self._fw(frame):
            return self._trace_lines
        return None

    def _trace_lines(self, frame, event, arg):
        if event == "line":
            self.lines += 1
        return self._trace_lines

    def _main(self):
        exit_reason = None
        if self.cpu is not None:
            sys.setprofile(self._profile)
            if self.cpu.per_line_us:
                sys.settrace(self._trace)   # line counting is slow; only when used
        try:
            self.body()
            exit_reason = "returned"
        except NodeHalted:
            exit_reason = "halted"
        except NodeReboot:
            exit_reason = "reboot"
        except BaseException as error:
            exit_reason = "error"
            self.sim.log(self.name, "ERROR", repr(error))
            for line in traceback.format_exc().splitlines():
                self.sim.log(self.name, "TB", line)
        finally:
            self.finished = True
            if self.on_exit:
                self.on_exit(exit_reason)
            self.sched.yielded.set()

    # --- called on the actor's own thread ---
    def _pending_us(self):
        cpu = self.cpu
        return ((self.calls - self.charged_calls) * cpu.per_call_us +
                (self.lines - self.charged_lines) * cpu.per_line_us)

    def now(self):
        pending = self._pending_us() if self.cpu is not None else 0
        return self.sched.now + self.debt + int(pending)

    def charge(self):
        if self.cpu is not None and (self.calls != self.charged_calls or self.lines != self.charged_lines):
            work = self._pending_us()
            self.gc_countdown -= self.calls - self.charged_calls
            self.charged_calls = self.calls
            self.charged_lines = self.lines
            self.debt += self.cpu.cost(work, self.sim.rng)
            if self.gc_countdown <= 0 and self.cpu.gc_pause_us:
                # MicroPython garbage collection pauses the whole loop.
                pause = self.cpu.cost(self.cpu.gc_pause_us, self.sim.rng)
                self.debt += pause
                self.gc_countdown = self.cpu.next_gc(self.sim.rng)
                self.sim.log(self.name, "GC", "%.1f ms pause" % (pause / 1000.0), t=self.sched.now + self.debt - pause)

    def park(self):
        self.sched.yielded.set()
        self.go.wait()
        self.go.clear()
        if self.halted:
            raise NodeHalted()

    def sleep_us(self, duration_us, state="sleep"):
        self.settle()
        self.state = state
        self.arm(self.sched.now + max(0, int(duration_us)))
        self.park()

    def settle(self):
        """Spend any outstanding CPU time; returns the current time."""
        self.charge()
        if self.debt > 20000:
            self.sim.log(self.name, "BUSY", "%.1f ms of CPU" % (self.debt / 1000.0))
        if self.debt:
            self.state = "cpu"
            target = self.sched.now + self.debt
            self.debt = 0
            self.arm(target)
            self.park()
        return self.sched.now


# ---------------------------------------------------------------------------
# Radio medium
# ---------------------------------------------------------------------------
class Transmission(object):
    def __init__(self, sender, start, end, payload, channel, address):
        self.sender = sender
        self.start = start
        self.end = end
        self.payload = payload
        self.channel = channel
        self.address = address
        self.rx_rssi = {}


class Medium(object):
    def __init__(self, sim, path_loss_exp=3.0, ref_loss_db=40.0, fading_db=2.0,
                 sensitivity_dbm=-93.0, floor_dbm=-110.0, capture_db=6.0):
        self.sim = sim
        self.path_loss_exp = path_loss_exp
        self.ref_loss_db = ref_loss_db
        self.fading_db = fading_db
        self.sensitivity_dbm = sensitivity_dbm
        self.floor_dbm = floor_dbm
        self.capture_db = capture_db
        self.overrides = {}     # (a, b) -> fixed mean rssi
        self.extra_loss = {}    # (a, b) -> dB added to the path loss
        self.recent = []

    def mean_rssi(self, sender, receiver, tx_power):
        key = (sender.name, receiver.name)
        if key in self.overrides:
            value = self.overrides[key]
            return None if value is None else value
        dx = sender.pos[0] - receiver.pos[0]
        dy = sender.pos[1] - receiver.pos[1]
        distance = max(1.0, math.hypot(dx, dy))
        loss = self.ref_loss_db + 10.0 * self.path_loss_exp * math.log10(distance)
        loss += self.extra_loss.get(key, 0.0)
        return tx_power - loss

    def transmit(self, node, payload):
        sched = self.sim.sched
        start = sched.now + RAMP_US
        end = start + airtime_us(len(payload))
        radio = node.radio
        tx = Transmission(node, start, end, bytes(payload), radio.channel, radio.address)
        for other in self.sim.nodes.values():
            if other is node or not other.alive or other.radio.channel != tx.channel:
                continue
            mean = self.mean_rssi(node, other, radio.tx_power)
            if mean is None:
                continue
            rssi = mean + self.sim.rng.gauss(0.0, self.fading_db)
            if rssi < self.floor_dbm:
                continue
            tx.rx_rssi[other] = rssi
            sched.at(start, lambda o=other: o.radio.on_air_start(tx))
            sched.at(end, lambda o=other: o.radio.on_air_end(tx))
        self.recent.append(tx)
        self.sim.tx_log.append((start, node.name, tx.payload))
        cutoff = sched.now - 50 * US_PER_MS
        if len(self.recent) > 64:
            self.recent = [t for t in self.recent if t.end >= cutoff]
        node.stats["tx"] += 1
        node.stats["airtime_us"] += end - start
        self.sim.log(node.name, "TX", "%dB %s" % (len(payload), describe_payload(payload)))
        return tx

    def overlapping(self, receiver, start, end, channel, exclude=None):
        for tx in self.recent:
            if tx is exclude or tx.channel != channel:
                continue
            if receiver not in tx.rx_rssi:
                continue
            if tx.start < end and tx.end > start:
                yield tx

    def channel_busy(self, node, start, end, busy_dbm):
        for tx in self.overlapping(node, start, end, node.radio.channel):
            if tx.rx_rssi[node] >= busy_dbm:
                return True
        return False


def describe_payload(payload):
    """Short human label for a radio payload (mesh v1, or pre-mesh)."""
    if len(payload) < 4:
        return payload.hex()
    net = int.from_bytes(payload[:2], "big")
    parsed = mesh.mesh_parse(payload)
    if parsed is not None:
        ctl, origin, msg_id, relay, age, offset = parsed
        head = "net=%04X %08X#%04X h%d%s%s" % (
            net, origin, msg_id, ctl & 3,
            (" via=%08X" % relay) if relay is not None else "",
            (" age=%d" % age) if age is not None else "")
    else:
        offset = 2
        seq = None
        if payload[2] != 0xFF and payload[3] == 0xFF:
            seq = payload[2]
            offset = 3
        head = "net=%04X legacy seq=%s" % (net, seq)
    parser = nfcp.NFCPStreamParser()
    pkt = None
    for b in payload[offset:]:
        pkt = parser.feed(b) or pkt
    if not pkt:
        return "%s raw=%s" % (head, payload.hex())
    return "%s %s" % (head, describe_packet(pkt))


CMD_NAMES = dict(
    (value, name[4:]) for name, value in vars(nfcp).items()
    if name.startswith("CMD_") and isinstance(value, int)
)
FRAME_NAMES = {
    nfcp.SOF_BROADCAST: "bcast",
    nfcp.SOF_GROUP: "group",
    nfcp.SOF_UNIQUE: "uid",
    nfcp.SOF_RESPONSE: "rsp",
}


def describe_packet(pkt):
    kind = FRAME_NAMES.get(pkt.get("frame_type"), "?")
    cmd = CMD_NAMES.get(pkt.get("cmd"), "0x%02X" % (pkt.get("cmd") or 0))
    target = pkt.get("target")
    src = pkt.get("src")
    if kind == "rsp":
        where = "from=%08X" % src
    elif target is None:
        where = ""
    elif kind == "group":
        where = "to=g%04X" % target
    else:
        where = "to=%08X" % target
    data = bytes(pkt.get("data") or b"")
    return "%s %s %s data=%s" % (kind, cmd, where, data[:12].hex())


# ---------------------------------------------------------------------------
# Per-node fake hardware
# ---------------------------------------------------------------------------
class FakeRadio(object):
    def __init__(self, node):
        self.node = node
        self.channel = None
        self.address = None
        self.tx_power = 8
        self.listening = False
        self.listen_since = 0
        self.lock = None
        self.result = None
        self.last_rssi = 0
        self.counters = dict.fromkeys((
            "tx_ok", "tx_timeout", "tx_busy", "rx_ok", "rx_timeout",
            "rx_crc_error", "rx_bad_len", "cca_ok", "cca_busy",
        ), 0)

    # --- scheduler-side callbacks ---
    def on_air_start(self, tx):
        node = self.node
        if not node.alive:
            return
        rssi = tx.rx_rssi[node]
        decodable = tx.address == self.address and rssi >= self.node.sim.medium.sensitivity_dbm
        if not decodable:
            return
        if self.listening and self.listen_since <= tx.start and self.lock is None:
            self.lock = tx
            self.last_rssi = int(round(rssi))
        elif not self.listening:
            node.stats["missed_deaf"] += 1
            node.sim.log(node.name, "RX_MISS", "deaf (%s) to %s from %s" % (
                node.actor.state if node.actor else "off", describe_payload(tx.payload), tx.sender.name))
        elif self.lock is not None:
            node.stats["missed_locked"] += 1
            node.sim.log(node.name, "RX_MISS", "busy receiving, lost %s from %s" % (
                describe_payload(tx.payload), tx.sender.name))
        else:
            node.stats["missed_deaf"] += 1
            node.sim.log(node.name, "RX_MISS", "rx ramping up, lost %s from %s" % (
                describe_payload(tx.payload), tx.sender.name))

    def on_air_end(self, tx):
        node = self.node
        if self.lock is not tx or not self.listening:
            return
        self.lock = None
        medium = node.sim.medium
        signal = tx.rx_rssi[node]
        noise_mw = 0.0
        for other in medium.overlapping(node, tx.start, tx.end, tx.channel, exclude=tx):
            noise_mw += 10.0 ** (other.rx_rssi[node] / 10.0)
        ok = True
        if noise_mw > 0.0:
            ok = signal - 10.0 * math.log10(noise_mw) >= medium.capture_db
        self.listening = False
        if ok:
            self.result = tx.payload
            self.counters["rx_ok"] += 1
            node.stats["rx"] += 1
        else:
            self.result = None
            self.counters["rx_crc_error"] += 1
            node.stats["rx_collision"] += 1
            node.sim.log(node.name, "RX_CRC", "collision on %s from %s" % (
                describe_payload(tx.payload), tx.sender.name))
        node.actor.arm(tx.end + 20)

    # --- firmware API (actor thread) ---
    def init(self, channel=90, tx_power=8, address=0x67676767):
        self.channel = channel
        self.tx_power = tx_power
        self.address = address
        self.node.sim.log(self.node.name, "RADIO_INIT", "ch=%s power=%s addr=0x%08X" % (
            channel, tx_power, address))

    def send(self, payload, cca_ms=0, busy_dbm=-85, backoff_ms=0):
        node = self.node
        if len(payload) > 252:
            raise ValueError("payload too long")
        actor = node.actor
        actor.debt += node.cpu.cost(node.cpu.tx_us, node.sim.rng)
        start = actor.settle()
        if self.channel is None:
            raise OSError("radio not initialised")
        if cca_ms:
            window_end = start + RAMP_US + int(cca_ms) * US_PER_MS
            actor.sleep_us(window_end - start, "cca")
            if node.sim.medium.channel_busy(node, start + RAMP_US, window_end, busy_dbm):
                self.counters["tx_busy"] += 1
                self.counters["cca_busy"] += 1
                node.stats["cca_busy"] += 1
                if backoff_ms:
                    actor.sleep_us(((int(node.clock_us()) % backoff_ms) + 1) * US_PER_MS, "backoff")
                return False
            self.counters["cca_ok"] += 1
        if not node.alive:
            return True
        tx = node.sim.medium.transmit(node, payload)
        actor.sleep_us(tx.end - node.sim.sched.now, "tx")
        self.counters["tx_ok"] += 1
        return True

    def recv(self, timeout_ms=0):
        node = self.node
        actor = node.actor
        actor.debt += node.cpu.cost(node.cpu.loop_us, node.sim.rng)
        start = actor.settle()
        self.result = None
        self.lock = None
        self.listening = True
        self.listen_since = start + RAMP_US
        if timeout_ms:
            # The C driver times out on whole ticks_ms of the node's own clock.
            start_ms = int(node.clock_us() // 1000)
            actor.arm(int(node.global_from_local_us((start_ms + int(timeout_ms)) * 1000)) + 1)
        else:
            actor.token += 1    # wait for a packet with no deadline
        actor.state = "rx"
        try:
            actor.park()
        finally:
            timed_out = self.listening
            self.listening = False
            self.lock = None
        if timed_out:
            self.counters["rx_timeout"] += 1
            return None
        payload = self.result
        self.result = None
        if payload is not None:
            # MicroPython processing time varies from packet to packet.
            actor.debt += node.cpu.cost(node.cpu.rx_us, node.sim.rng)
        return payload


def make_radio_module(node):
    radio = node.radio
    module = types.ModuleType("nrf_radio")
    module.init = radio.init
    module.send = radio.send
    module.recv = radio.recv
    module.last_rssi = lambda: radio.last_rssi
    module.device_id32 = lambda: node.device_id
    module.device_id = lambda: node.device_id.to_bytes(4, "big") * 2

    def stats():
        data = dict(radio.counters)
        data.update(last_rssi=radio.last_rssi, channel=radio.channel or 0,
                    tx_power=radio.tx_power, address=radio.address or 0, max_payload=252)
        return data

    def clear_stats():
        for key in radio.counters:
            radio.counters[key] = 0

    def channel(value):
        radio.channel = value

    def noise_sample(channel, duration_ms=100, interval_ms=2, busy_dbm=-85):
        node.actor.sleep_us(duration_ms * US_PER_MS)
        return {"channel": channel, "count": 1, "avg": -100, "min": -100, "max": -100, "busy_count": 0}

    module.stats = stats
    module.clear_stats = clear_stats
    module.channel = channel
    module.noise_sample = noise_sample
    module.write_ws2811 = lambda *args: None
    return module


def make_time_module(node):
    module = types.ModuleType("time")
    module.ticks_ms = lambda: int(node.clock_us() // 1000) % TICKS_PERIOD
    module.ticks_us = lambda: int(node.clock_us()) % TICKS_PERIOD
    module.ticks_cpu = module.ticks_us
    module.ticks_add = lambda a, b: (a + b) % TICKS_PERIOD

    def ticks_diff(a, b):
        half = TICKS_PERIOD // 2
        return ((a - b + half) % TICKS_PERIOD) - half

    module.ticks_diff = ticks_diff

    def sleep_ms(ms):
        node.actor.sleep_us(node.local_to_global_us(int(ms) * US_PER_MS))

    def sleep_us(us):
        # Short busy-waits are charged as CPU time instead of yielding.
        node.actor.debt += node.local_to_global_us(int(us))

    module.sleep_ms = sleep_ms
    module.sleep_us = sleep_us
    module.sleep = lambda s: sleep_ms(int(s * 1000))
    module.time = lambda: int(node.clock_us() // 1000000)
    return module


def make_machine_module(node):
    module = types.ModuleType("machine")
    levels = node.pins

    class Pin(object):
        IN = 0
        OUT = 1
        PULL_UP = 1
        PULL_DOWN = 2
        OPEN_DRAIN = 3
        IRQ_FALLING = 1
        IRQ_RISING = 2

        def __init__(self, pin_id, mode=None, pull=None, value=None):
            self.id = pin_id
            if value is not None:
                self.value(value)
            elif pin_id not in levels:
                levels[pin_id] = 1

        def init(self, *args, **kwargs):
            pass

        def value(self, v=None):
            if v is None:
                return levels.get(self.id, 1)
            levels[self.id] = 1 if v else 0
            return None

        def on(self):
            self.value(1)

        def off(self):
            self.value(0)

        def irq(self, *args, **kwargs):
            return None

        def __call__(self, v=None):
            return self.value(v)

    class ADC(object):
        def __init__(self, pin, *args, **kwargs):
            self.pin = pin

        def read_u16(self):
            return node.adc_reading

    class I2C(object):
        def __init__(self, *args, **kwargs):
            pass

        def scan(self):
            return []

        def writeto(self, *args, **kwargs):
            return None

    class UART(object):
        def __init__(self, *args, **kwargs):
            pass

        def any(self):
            return len(node.uart_rx)

        def read(self, n=None):
            if not node.uart_rx:
                return None
            n = len(node.uart_rx) if n is None else n
            data = bytes(node.uart_rx[:n])
            del node.uart_rx[:n]
            return data

        def write(self, data):
            if node.host is not None:
                node.host.from_bridge(bytes(data))
            return len(data)

    def reset():
        node.sim.log(node.name, "RESET")
        raise NodeReboot()

    def bootloader():
        node.sim.log(node.name, "BOOTLOADER")
        raise NodeHalted()

    module.Pin = Pin
    module.ADC = ADC
    module.I2C = I2C
    module.UART = UART
    module.reset = reset
    module.soft_reset = reset
    module.bootloader = bootloader
    module.unique_id = lambda: b"\x00\x00" + node.device_id.to_bytes(4, "big")
    module.freq = lambda *args: 64000000
    module.idle = lambda: None
    return module


class VirtualFile(object):
    def __init__(self, files, path, mode):
        self.files = files
        self.path = path
        self.binary = "b" in mode
        self.writing = any(flag in mode for flag in "wa+")
        existing = files.get(path)
        if "r" in mode and existing is None:
            raise OSError(2, "ENOENT")
        if "w" in mode:
            existing = b""
        existing = existing or b""
        self.buf = io.BytesIO(existing) if self.binary else io.StringIO(existing.decode())
        if "a" in mode:
            self.buf.seek(0, 2)

    def __getattr__(self, name):
        return getattr(self.buf, name)

    def __iter__(self):
        return iter(self.buf)

    def close(self):
        if self.writing:
            value = self.buf.getvalue()
            self.files[self.path] = value if self.binary else value.encode()
        self.buf.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def make_os_module(node):
    module = types.ModuleType("os")
    files = node.files

    def remove(path):
        if path not in files:
            raise OSError(2, "ENOENT")
        del files[path]

    def rename(src, dst):
        if src not in files:
            raise OSError(2, "ENOENT")
        files[dst] = files.pop(src)

    def stat(path):
        if path not in files:
            raise OSError(2, "ENOENT")
        return (0x8000, 0, 0, 0, 0, 0, len(files[path]), 0, 0, 0)

    module.remove = remove
    module.rename = rename
    module.stat = stat
    module.listdir = lambda path="": sorted(files)
    module.mkdir = lambda path: None
    module.sync = lambda: None
    module.getcwd = lambda: "/"
    module.sep = "/"
    return module


def make_random_module(node):
    rng = random.Random(node.sim.rng.getrandbits(32))
    module = types.ModuleType("urandom")
    module.getrandbits = rng.getrandbits
    module.randint = rng.randint
    module.random = rng.random
    module.uniform = rng.uniform
    module.choice = rng.choice
    module.seed = rng.seed
    module.randrange = rng.randrange
    return module


def make_gc_module():
    module = types.ModuleType("gc")
    module.collect = lambda: None
    module.mem_free = lambda: 100000
    module.mem_alloc = lambda: 20000
    module.enable = lambda: None
    module.disable = lambda: None
    module.threshold = lambda *args: None
    return module


def make_micropython_module():
    module = types.ModuleType("micropython")
    module.const = lambda value: value
    module.mem_info = lambda *args: None
    module.alloc_emergency_exception_buf = lambda size: None
    module.schedule = lambda fn, arg: fn(arg)
    return module


_CODE_CACHE = {}


FW_PREFIX = "fw:"   # marks code a node loaded, so only that counts as its CPU work


def compiled(path, source=None):
    key = path if source is None else (path, source)
    code = _CODE_CACHE.get(key)
    if code is None:
        if source is None:
            with io.open(path, encoding="utf-8") as handle:
                source = handle.read()
        # The simulator itself imports nfcp/mesh/bridge_radio from main/ to
        # decode traffic; the prefix keeps that work off the nodes' CPU.
        code = compile(source, FW_PREFIX + path, "exec")
        _CODE_CACHE[key] = code
    return code


class CpuModel(object):
    """Virtual CPU time charged to a node.

    Firmware work = Python and C calls the firmware makes x `per_call_us`,
    counted with a profiler hook (plus executed lines x `per_line_us`, which
    is off by default). The fixed costs cover the C radio driver. Garbage
    collection pauses the node for `gc_pause_us` about every
    `gc_every_calls` calls.

    Calibrated 2026-11-06 on a nice!nano nRF52840 field module (MicroPython
    1.29 preview) with scripts/diagnostics/mesh_cpu_benchmark.py, over two
    runs: the pre-mesh receive path and the mesh v1 receive path.
    - 116-byte frame for another node: 43.2 ms pre-mesh, 3.75 ms mesh.
    - 14-byte frame for another node: 4.5 ms pre-mesh, 2.95 ms mesh.
    - Duplicate: 4.8 ms pre-mesh, 0.70 ms mesh.
    - USB frame parse on the bridge (unchanged code): 7.1 ms and 9.0 ms, so
      run-to-run spread on the module is about 25%.
    - gc.collect(): 41-49 ms.
    A flat 92 us per call fits all 14 workloads within about 30%. Adding a
    per-line or per-bytecode term did not fit better: the pre-mesh code was
    loop-heavy and the mesh code is allocation-heavy. `--cpu-scale`
    multiplies all costs."""

    def __init__(self, per_call_us=92.0, per_line_us=0.0, rx_us=0, tx_us=150, loop_us=30,
                 gc_every_calls=25000, gc_pause_us=45000, scale=1.0):
        self.per_call_us = per_call_us * scale
        self.per_line_us = per_line_us * scale
        self.gc_every_calls = gc_every_calls
        self.gc_pause_us = int(gc_pause_us * scale)
        self.rx_us = int(rx_us * scale)
        self.tx_us = int(tx_us * scale)
        self.loop_us = int(loop_us * scale)

    def next_gc(self, rng):
        return int(self.gc_every_calls * rng.uniform(0.5, 1.5))

    @staticmethod
    def cost(base_us, rng):
        # MicroPython timing varies (allocation, GC, interrupts). Without this
        # spread, identical loop periods phase-lock nodes in the simulation.
        if not base_us:
            return 0
        return int(base_us * rng.uniform(0.8, 1.25))


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
ENTRY_POINTS = {
    "bridge": ("nrf_bridge_main", "nrf_bridge_config", False),
    "dummy": ("nrf_dummy_main", "nrf_node_config", True),
    "field": ("field_main", "nrf_node_config", False),
    "audio": ("sim_audio_node", "nrf_node_config", True),
}

FIELD_DEFAULT_CONFIG = {
    "ARM_CONTROL_PIN": 5,
    "CUE_COUNT": 16,
    "LOG_FIRE_EVENTS": True,
    "LCD_ENABLED": False,
}
DUMMY_DEFAULT_CONFIG = {"LCD_ENABLED": False}


class Node(object):
    def __init__(self, sim, name, kind, pos, device_id, config, clock_ppm, clock_offset_us,
                 boot_ms, cpu, src_dir):
        self.sim = sim
        self.name = name
        self.kind = kind
        self.pos = tuple(pos)
        self.device_id = device_id & 0xFFFFFFFF
        self.clock_ppm = clock_ppm
        self.clock_offset_us = clock_offset_us
        self.boot_ms = boot_ms
        self.cpu = cpu
        self.src_dir = src_dir
        self.alive = True
        self.files = {}
        self.pins = {}
        self.adc_reading = 40000
        self.uart_rx = bytearray()
        self.host = None
        self.radio = FakeRadio(self)
        self.stats = dict.fromkeys((
            "tx", "rx", "rx_collision", "missed_deaf", "missed_locked", "cca_busy",
            "airtime_us", "boots",
        ), 0)
        self.prints = []
        self.actor = None
        self.modules = {}
        entry, config_name, _ = ENTRY_POINTS[kind]
        self.files[config_name + ".py"] = config_source(config).encode()

    # --- clock ---
    def clock_us(self):
        t = self.actor.now() if self.actor else self.sim.sched.now
        return t * (1.0 + self.clock_ppm * 1e-6) + self.clock_offset_us

    def local_to_global_us(self, local_us):
        return local_us / (1.0 + self.clock_ppm * 1e-6)

    def global_from_local_us(self, clock_us):
        """Global time at which this node's clock reads `clock_us`."""
        return (clock_us - self.clock_offset_us) / (1.0 + self.clock_ppm * 1e-6)

    # --- sandboxed imports ---
    def _builtins(self):
        table = dict(vars(builtins))
        table["__import__"] = self._import
        table["open"] = lambda path, mode="r", *args, **kwargs: VirtualFile(self.files, path, mode)
        table["print"] = self._print
        return table

    def _print(self, *args, **kwargs):
        sep = kwargs.get("sep", " ")
        text = sep.join(str(a) for a in args)
        for line in text.splitlines() or [""]:
            if line.strip():
                t = self.actor.now() if self.actor else self.sim.sched.now
                self.prints.append((t, line))
                self.sim.log(self.name, "PRINT", line, t=t)

    def _import(self, name, globals=None, locals=None, fromlist=(), level=0):
        module = self.modules.get(name)
        if module is not None:
            return module
        fake = self.fakes.get(name)
        if fake is not None:
            return fake
        source = self.files.get(name + ".py")
        path = None
        if source is None:
            for directory in (self.src_dir, MAIN_DIR, SIM_DIR, AUDIO_SIM_DIR):
                candidate = os.path.join(directory, name + ".py")
                if os.path.exists(candidate):
                    path = candidate
                    break
        if source is not None or path is not None:
            module = types.ModuleType(name)
            module.__dict__["__builtins__"] = self.builtins
            module.__file__ = path or name + ".py"
            self.modules[name] = module
            try:
                if source is not None:
                    code = compiled("<%s flash>/%s.py" % (self.name, name), source.decode())
                else:
                    code = compiled(path)
                exec(code, module.__dict__)
            except BaseException:
                self.modules.pop(name, None)
                raise
            return module
        return builtins.__import__(name, globals, locals, fromlist, level)

    def boot(self, t_us):
        self.stats["boots"] += 1
        self.modules = {}
        self.uart_rx = bytearray()
        self.radio = FakeRadio(self)
        time_module = make_time_module(self)
        os_module = make_os_module(self)
        random_module = make_random_module(self)
        self.fakes = {
            "time": time_module, "utime": time_module,
            "machine": make_machine_module(self),
            "nrf_radio": make_radio_module(self),
            "os": os_module, "uos": os_module,
            "urandom": random_module, "random": random_module,
            "gc": make_gc_module(),
            "micropython": make_micropython_module(),
        }
        self.builtins = self._builtins()
        entry, _, call_main = ENTRY_POINTS[self.kind]

        def body():
            module = self._import(entry)
            if call_main and hasattr(module, "main"):
                module.main()

        self.actor = Actor(self.sim, self.name, body, cpu=self.cpu)

        def on_exit(reason):
            self.sim.log(self.name, "EXIT", reason)
            if reason == "reboot" and self.alive:
                self.sim.sched.at(self.sim.sched.now + self.boot_ms * US_PER_MS,
                                  lambda: self.boot(self.sim.sched.now))

        self.actor.on_exit = on_exit
        self.actor.start(t_us)

    def kill(self):
        self.alive = False
        if self.actor:
            self.actor.halt()
        self.radio.listening = False
        self.sim.log(self.name, "EVENT", "powered off")

    def revive(self):
        if self.alive:
            return
        self.alive = True
        self.sim.log(self.name, "EVENT", "powered on")
        self.boot(self.sim.sched.now + self.boot_ms * US_PER_MS)


def config_source(config):
    lines = ["# generated by mesh_sim"]
    for key in sorted(config):
        lines.append("%s = %r" % (key, config[key]))
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Host: the app side of the bridge USB link
# ---------------------------------------------------------------------------
class Host(object):
    def __init__(self, sim, bridge):
        self.sim = sim
        self.bridge = bridge
        bridge.host = self
        self.parser = bridge_radio.BridgeFrameParser()
        self.seq = 0
        self.rx = []        # (t_us, rssi, pkt)
        self.replies = []   # (t_us, frame)

    def from_bridge(self, data):
        t = self.bridge.actor.now()
        for b in data:
            frame = self.parser.feed(b)
            if frame is None:
                continue
            if frame.cmd == bridge_radio.CMD_NFCP_RX:
                info = bridge_radio.parse_nfcp_rx(frame.payload)
                parser = nfcp.NFCPStreamParser()
                pkt = None
                for byte in info["nfcp_frame"]:
                    pkt = parser.feed(byte) or pkt
                if pkt:
                    self.rx.append((t, info["rssi"], pkt))
                    self.sim.log("app", "APP_RX", "rssi=%d %s" % (info["rssi"], describe_packet(pkt)), t=t)
            else:
                self.replies.append((t, frame))

    def send_nfcp(self, frame):
        self.seq = (self.seq + 1) & 0xFF
        if self.seq in (0, 0xFF):
            self.seq = 1
        self.bridge.uart_rx += bridge_radio.build_send_nfcp(self.seq, frame)
        return self.seq

    def send_reliable(self, uid, frame, timeout_ms):
        """CMD_SEND_NFCP_RELIABLE; returns the USB seq to find its answer."""
        self.seq = (self.seq + 1) & 0xFF
        if self.seq in (0, 0xFF):
            self.seq = 1
        payload = (uid & 0xFFFFFFFF).to_bytes(4, "big") + (int(timeout_ms) & 0xFFFF).to_bytes(2, "big") + frame
        self.bridge.uart_rx += bridge_radio.build_frame(self.seq, bridge_radio.CMD_SEND_NFCP_RELIABLE, payload)
        return self.seq


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------
class MeshSim(object):
    def __init__(self, seed=1, src_dir=MAIN_DIR, cpu_scale=1.0, verbose=False, **medium_args):
        self.sched = Scheduler()
        self.rng = random.Random(seed)
        self.seed = seed
        self.src_dir = src_dir
        self.cpu_scale = cpu_scale
        self.verbose = verbose
        self.medium = Medium(self, **medium_args)
        self.nodes = {}
        self.trace = []
        self.tx_log = []
        self.host = None
        self.results = []
        self.reliable = True       # app requests use CMD_SEND_NFCP_RELIABLE
        self.deliveries = []       # (label, status, route, attempts)
        self._next_id = 0x5A000001

    # --- logging ---
    def log(self, who, kind, *parts, **kwargs):
        t = kwargs.get("t", self.sched.now)
        line = "%10.3f %-8s %-10s %s" % (t / 1000.0, who, kind, " ".join(str(p) for p in parts))
        self.trace.append(line)
        if self.verbose:
            print(line)

    # --- building ---
    def add_node(self, name, kind, pos, device_id=None, config=None, clock_ppm=None, boot_ms=None):
        if device_id is None:
            device_id = self._next_id
            self._next_id += 0x111
        merged = {}
        if kind == "field":
            merged.update(FIELD_DEFAULT_CONFIG)
        elif kind == "dummy":
            merged.update(DUMMY_DEFAULT_CONFIG)
        merged.update(config or {})
        if clock_ppm is None:
            clock_ppm = self.rng.uniform(-30.0, 30.0)
        node = Node(
            self, name, kind, pos, device_id, merged, clock_ppm,
            clock_offset_us=self.rng.randrange(0, 600 * 1000 * 1000),
            boot_ms=self.rng.randrange(300, 900) if boot_ms is None else boot_ms,
            cpu=CpuModel(scale=self.cpu_scale),
            src_dir=self.src_dir,
        )
        self.nodes[name] = node
        if kind == "bridge":
            self.host = Host(self, node)
        return node

    def add_bridge(self, name="bridge", pos=(0, 0), **kwargs):
        return self.add_node(name, "bridge", pos, **kwargs)

    def add_module(self, name, pos, fw="dummy", **kwargs):
        return self.add_node(name, fw, pos, **kwargs)

    # --- topology changes (any time) ---
    def set_link(self, a, b, rssi, both=True):
        """Pin the mean RSSI between two nodes; None cuts the link."""
        self.medium.overrides[(a, b)] = rssi
        if both:
            self.medium.overrides[(b, a)] = rssi
        self.log("sim", "EVENT", "link %s-%s rssi=%s" % (a, b, rssi))

    def obstacle(self, a, b, loss_db, both=True):
        self.medium.extra_loss[(a, b)] = loss_db
        if both:
            self.medium.extra_loss[(b, a)] = loss_db
        self.log("sim", "EVENT", "obstacle %s-%s %+.0f dB" % (a, b, loss_db))

    def move(self, name, pos):
        self.nodes[name].pos = tuple(pos)
        self.log(name, "EVENT", "moved to %s" % (pos,))

    def kill(self, name):
        self.nodes[name].kill()

    def revive(self, name):
        self.nodes[name].revive()

    def link_table(self):
        names = sorted(self.nodes)
        rows = []
        for a in names:
            for b in names:
                if a >= b:
                    continue
                na, nb = self.nodes[a], self.nodes[b]
                rssi = self.medium.mean_rssi(na, nb, 8)
                rows.append((a, b, rssi))
        return rows

    # --- results ---
    def check(self, label, ok, detail=""):
        self.results.append((label, bool(ok), detail))
        self.log("check", "PASS" if ok else "FAIL", label, detail)

    def note(self, label, detail):
        self.results.append((label, None, detail))
        self.log("check", "NOTE", label, detail)

    # --- running ---
    def run(self, scenario, duration_ms):
        for node in self.nodes.values():
            node.boot(node.boot_ms * US_PER_MS)
        app = App(self)
        app_actor = Actor(self, "app", lambda: scenario(self, app))
        app.actor = app_actor
        app_actor.start(2000 * US_PER_MS)
        self.sched.run_until(duration_ms * US_PER_MS, stop=lambda: app_actor.finished)
        for node in self.nodes.values():
            if node.actor:
                node.actor.halt()
        return app


# ---------------------------------------------------------------------------
# App-side helpers used by scenarios (run on the "app" actor thread)
# ---------------------------------------------------------------------------
class App(object):
    def __init__(self, sim):
        self.sim = sim
        self.host = sim.host
        self.master = nfcp.NFCP(node_id=0x01)
        self.actor = None
        self.last_frame = b""

    def now_ms(self):
        return self.actor.now() / 1000.0

    def sleep(self, ms):
        self.actor.sleep_us(int(ms * US_PER_MS))

    def send(self, frame, label):
        self.sim.log("app", "APP_TX", label)
        self.host.send_nfcp(frame)
        self.last_frame = bytes(frame)
        return self.actor.now()

    def wait_rx(self, predicate, timeout_ms, since=None):
        since = self.actor.now() if since is None else since
        deadline = self.actor.now() + timeout_ms * US_PER_MS
        while True:
            for t, rssi, pkt in self.host.rx:
                if t >= since and predicate(pkt):
                    return pkt
            if self.actor.now() >= deadline:
                return None
            self.sleep(5)

    def reply(self, uid, cmd, timeout_ms=1500, acked_cmd=None):
        """Wait for a response frame from `uid` with command `cmd`. For an ACK,
        `acked_cmd` must match the acknowledged command, as the app checks."""
        def match(p):
            if p.get("frame_type") != nfcp.SOF_RESPONSE or p.get("src") != uid or p.get("cmd") != cmd:
                return False
            if acked_cmd is not None:
                data = bytes(p.get("data") or b"")
                return len(data) == 2 and data[0] == acked_cmd and data[1] == 0
            return True
        return lambda since: self.wait_rx(match, timeout_ms, since)

    # --- commands ---
    def discover(self, window_ms=1200, nonce=0x1234):
        since = self.send(self.master.build_discover_broadcast(nonce=nonce), "discover")
        self.sleep(window_ms)
        found = {}
        for t, rssi, pkt in self.host.rx:
            if t >= since and pkt.get("cmd") == nfcp.CMD_DISCOVER_RSP:
                found.setdefault(pkt["src"], rssi)
        return found

    def request(self, uid, frame, label, reply_cmd, timeout_ms=1500, acked_cmd=None):
        """A unicast request and its reply. With sim.reliable (default) it
        goes through the bridge's reliable delivery (Phase 3), otherwise as
        a single SEND_NFCP like the pre-Phase 3 app."""
        if not self.sim.reliable:
            since = self.send(frame, label)
            return self.reply(uid, reply_cmd, timeout_ms, acked_cmd)(since)
        self.sim.log("app", "APP_TX", label + " (reliable)")
        since = self.actor.now()
        seq = self.host.send_reliable(uid, frame, timeout_ms)
        self.last_frame = bytes(frame)
        pkt = self.reply(uid, reply_cmd, timeout_ms + 300, acked_cmd)(since)
        for t, answer in self.host.replies:
            if t >= since and answer.seq == seq and answer.cmd == bridge_radio.CMD_SEND_NFCP_RELIABLE:
                result = bytes(answer.payload)
                if len(result) >= 7:
                    self.sim.log("app", "DELIVERY", "%s status=%d route=%s relay=%08X attempts=%d" % (
                        label, result[0], ("direct", "via", "flood")[result[1]] if result[1] < 3 else result[1],
                        int.from_bytes(result[2:6], "big"), result[6]))
                    self.sim.deliveries.append((label, result[0], result[1], result[6]))
        return pkt

    def ack(self, uid, frame, label, cmd, timeout_ms=1500):
        """Send a command and wait for its ACK; True if acknowledged."""
        return self.request(uid, frame, label, nfcp.CMD_ACK, timeout_ms, acked_cmd=cmd) is not None

    def ack_code(self, uid, frame, label, cmd, timeout_ms=1500):
        """Send a command; the code of its ACK (ACK_OK, ACK_CHASE...) or None."""
        pkt = self.request(uid, frame, label, nfcp.CMD_ACK, timeout_ms)
        data = bytes(pkt.get("data") or b"") if pkt else b""
        return data[1] if len(data) == 2 and data[0] == cmd else None

    def info(self, uid):
        return self.request(uid, self.master.build_info_request(uid), "info %08X" % uid, nfcp.CMD_INFO_RSP)

    def arm(self, uid):
        return self.ack(uid, self.master.build_arm_unique(uid), "arm %08X" % uid, nfcp.CMD_ARM)

    def disarm(self, uid):
        return self.ack(uid, self.master.build_disarm_unique(uid), "disarm %08X" % uid, nfcp.CMD_DISARM)

    def fire_unique(self, uid, mask):
        return self.send(self.master.build_unique_fire(uid, mask), "fire %08X mask=0x%X" % (uid, mask))

    def fire_group(self, group, mask):
        return self.send(self.master.build_group_fire(group, mask), "fire group %04X mask=0x%X" % (group, mask))

    def broadcast(self, frame, label):
        return self.send(frame, label)


def first_print(node, pattern, since_us):
    regex = re.compile(pattern) if isinstance(pattern, str) else pattern
    for t, line in node.prints:
        if t >= since_us and regex.search(line):
            return t, line
    return None


def node_tx_after(sim, name, since_us, contains=None):
    """Start time of the first transmission by `name` at or after `since_us`
    (whose payload contains `contains`, if given)."""
    for start, sender, payload in sim.tx_log:
        if sender == name and start >= since_us and (contains is None or contains in payload):
            return start
    return None


def format_results(sim, name):
    out = []
    passed = sum(1 for _, ok, _ in sim.results if ok is True)
    failed = sum(1 for _, ok, _ in sim.results if ok is False)
    out.append("== %s (seed %d): %d pass, %d fail" % (name, sim.seed, passed, failed))
    for label, ok, detail in sim.results:
        mark = "NOTE" if ok is None else ("PASS" if ok else "FAIL")
        out.append("  %-4s %-38s %s" % (mark, label, detail))
    out.append("  radio:  node      tx  rx  coll  deaf  locked  cca_busy  air_ms  boots")
    for name_, node in sorted(sim.nodes.items()):
        s = node.stats
        out.append("          %-8s %3d %3d  %4d  %4d  %6d  %8d  %6.1f  %5d" % (
            name_, s["tx"], s["rx"], s["rx_collision"], s["missed_deaf"], s["missed_locked"],
            s["cca_busy"], s["airtime_us"] / 1000.0, s["boots"]))
    errors = [line for line in sim.trace if " ERROR " in line]
    for line in errors[:10]:
        out.append("  ERROR " + line)
    return "\n".join(out)


def main():
    import mesh_scenarios

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="all")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--src", default=MAIN_DIR, help="folder holding the firmware entry files")
    parser.add_argument("--cpu-scale", type=float, default=1.0, help="multiply the MicroPython CPU cost model")
    parser.add_argument("--out", help="trace file (with --scenario all, a folder)")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--plain", action="store_true",
                        help="app sends requests once (SEND_NFCP), as before Phase 3")
    args = parser.parse_args()

    if args.list:
        for name, fn in mesh_scenarios.SCENARIOS.items():
            print("%-12s %s" % (name, (fn.__doc__ or "").strip().splitlines()[0]))
        return 0

    names = list(mesh_scenarios.SCENARIOS) if args.scenario == "all" else [args.scenario]
    failures = 0
    for name in names:
        build, duration_ms = mesh_scenarios.SCENARIOS[name], None
        sim = MeshSim(seed=args.seed, src_dir=os.path.abspath(args.src),
                      cpu_scale=args.cpu_scale, verbose=args.verbose)
        sim.reliable = not args.plain
        scenario, duration_ms = build(sim)
        sim.run(scenario, duration_ms)
        print(format_results(sim, name))
        failures += sum(1 for _, ok, _ in sim.results if ok is False)
        if args.out:
            path = args.out
            if len(names) > 1:
                os.makedirs(path, exist_ok=True)
                path = os.path.join(path, name + ".trace")
            with io.open(path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(sim.trace) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
