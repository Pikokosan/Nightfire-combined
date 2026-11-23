"""Run the USB bridge firmware on a PC against fake hardware.

The bridge (main/nrf_usb_monitor_main.py and the modules it imports) is
driven through a fixed scenario: every USB command, sessions and their
expiry, arming and disarming through the ARM key, reliable delivery with and
without a reply, pairing, maintenance, a main-loop error and a reset.
Everything it does is written to a trace: USB replies, radio transmissions,
channel changes, LED writes and console prints. Comparing traces from two
versions of the firmware shows whether a refactor changed behaviour.

    py -3 scripts/sim/bridge_trace_harness.py --mode master --out master.trace
    py -3 scripts/sim/bridge_trace_harness.py --mode monitor --src old_main_dir --out old.trace

--src names a folder of bridge sources to run instead of main/ (any module
missing there comes from main/). --mode monitor runs without MASTER_ENABLED.
"""
from __future__ import print_function

import argparse
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_DIR = os.path.join(ROOT, "main")
TICKS_PERIOD = 1 << 30
START_MS = 1000


def load_helper(name):
    """A copy of a main/ module for building test frames, kept apart from
    the modules the firmware imports."""
    spec = importlib.util.spec_from_file_location("harness_" + name, os.path.join(MAIN_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H_BRIDGE = load_helper("bridge_radio")
H_NFCP = load_helper("nfcp")
H_MESH = load_helper("mesh")

TRACE = []
_clock = {"ms": START_MS}


class StopHarness(BaseException):
    """Ends the bridge's endless main loop (not caught by `except Exception`)."""


def now():
    return _clock["ms"]


def log(kind, *parts):
    TRACE.append("%8d %s %s" % (now(), kind, " ".join(str(p) for p in parts)))


# --- fake time --------------------------------------------------------------
def _ticks_diff(a, b):
    half = TICKS_PERIOD // 2
    return ((a - b + half) % TICKS_PERIOD) - half


def _sleep_ms(ms):
    _clock["ms"] += int(ms)


fake_time = types.ModuleType("time")
fake_time.ticks_ms = lambda: _clock["ms"] % TICKS_PERIOD
fake_time.ticks_us = lambda: (_clock["ms"] * 1000) % TICKS_PERIOD
fake_time.ticks_add = lambda a, b: (a + b) % TICKS_PERIOD
fake_time.ticks_diff = _ticks_diff
fake_time.sleep_ms = _sleep_ms
fake_time.sleep_us = lambda us: None
fake_time.sleep = lambda s: _sleep_ms(int(s * 1000))
fake_time.time = lambda: _clock["ms"] // 1000


# --- fake machine -----------------------------------------------------------
class Pin(object):
    IN = 0
    OUT = 1
    PULL_UP = 1
    levels = {}

    def __init__(self, pin_id, mode=None, pull=None, value=None):
        self.id = pin_id
        if value is not None:
            self.value(value)
        elif pin_id not in Pin.levels:
            Pin.levels[pin_id] = 1

    def value(self, v=None):
        if v is None:
            return Pin.levels.get(self.id, 1)
        v = 1 if v else 0
        if Pin.levels.get(self.id) != v:
            log("PIN", "P%s=%d" % (self.id, v))
        Pin.levels[self.id] = v
        return None


def _reset():
    log("RESET")
    raise StopHarness()


fake_machine = types.ModuleType("machine")
fake_machine.Pin = Pin
fake_machine.reset = _reset


# --- fake radio -------------------------------------------------------------
class Radio(object):
    def __init__(self):
        self.inbox = []          # packets recv() returns, oldest first
        self.fail_next_recv = False
        self.last_tx = None

    def init(self, channel=None, tx_power=None, address=None):
        log("RADIO_INIT", "ch=%s power=%s addr=0x%08X" % (channel, tx_power, address))

    def send(self, payload, cca_ms=None, busy_dbm=None, backoff_ms=None):
        self.last_tx = bytes(payload)
        log("TX", bytes(payload).hex())
        return True

    def recv(self, timeout=0):
        if self.fail_next_recv:
            self.fail_next_recv = False
            raise OSError("fake radio receive error")
        if self.inbox:
            return self.inbox.pop(0)
        _sleep_ms(max(0, int(timeout)))
        return None

    def last_rssi(self):
        return -61

    def device_id32(self):
        return 0xB0B0B0B0

    def stats(self):
        return {"tx_ok": 7, "tx_timeout": 1, "rx_ok": 5, "rx_timeout": 2, "rx_crc_error": 0,
                "rx_bad_len": 0, "channel": 90, "tx_power": 8, "last_rssi": -61,
                "max_payload": 252, "address": 0x6D657368}

    def clear_stats(self):
        log("RADIO_CLEAR_STATS")

    def channel(self, value):
        log("RADIO_CHANNEL", value)

    def noise_sample(self, channel, duration_ms=45, interval_ms=3):
        level = -100 + (channel * 7) % 20
        return {"avg": level, "min": level - 3, "max": level + 4, "count": 15,
                "busy_count": 1 if level + 4 >= -85 else 0}

    def write_ws2811(self, pin, data, timing):
        log("LED", "P%d %s" % (pin, bytes(data).hex()))


RADIO = Radio()


# --- fake USB transport -----------------------------------------------------
class Transport(object):
    def __init__(self):
        self.pending = bytearray()
        self.parser = H_BRIDGE.BridgeFrameParser()

    def any(self):
        DRIVER.tick()            # the start of every main-loop pass
        return bool(self.pending)

    def read(self, count):
        data = bytes(self.pending[:count])
        del self.pending[:count]
        return data

    def write(self, data):
        for frame in self.parser.feed_bytes(bytes(data)):
            log("USB", "seq=%d cmd=0x%02X flags=0x%02X %s" %
                (frame.seq, frame.cmd, frame.flags, bytes(frame.payload).hex()))
        return len(data)


TRANSPORT = Transport()
fake_transport = types.ModuleType("usb_bridge_transport")
fake_transport.UsbHexTransport = lambda: TRANSPORT


class Printer(io.StringIO):
    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                log("PRINT", line)
        return len(text)


# --- scenario ---------------------------------------------------------------
UID = 0x2A2A0001
UID_FF = 0x11FF22FF      # escaping: 0xFF bytes in the NFCP frame
NETWORK = 0x04D2
P = H_NFCP.NFCP(node_id=0x01)
ARM_SWITCH_PIN = 6


def reliable(uid, frame, timeout_ms=900):
    return H_BRIDGE.int_to_be(uid, 4) + H_BRIDGE.int_to_be(timeout_ms, 2) + frame


def module_frame(uid, frame, network=NETWORK, msg_id=None):
    """A radio packet from module uid: a fresh frame, or the reply to the
    bridge's last transmission (its msg_id)."""
    if msg_id is None:
        msg_id = H_MESH.mesh_parse(RADIO.last_tx)[2]
    return H_MESH.mesh_build(network, uid, msg_id, frame)


class Driver(object):
    def __init__(self):
        self.events = []         # (at_ms, order, action)
        self.seq = 0

    def at(self, ms, action):
        self.events.append((START_MS + ms, len(self.events), action))

    def host(self, ms, cmd, payload=b""):
        def send():
            self.seq = (self.seq + 1) & 0xFF
            log("HOST", "seq=%d cmd=0x%02X %s" % (self.seq, cmd, bytes(payload).hex()))
            TRANSPORT.pending += H_BRIDGE.build_frame(self.seq, cmd, payload)
        self.at(ms, send)

    def radio(self, ms, make_packet, label):
        def receive():
            packet = make_packet()
            log("RX", label, bytes(packet).hex())
            RADIO.inbox.append(packet)
        self.at(ms, receive)

    def switch(self, ms, armed):
        def flip():
            log("SWITCH", "ARM" if armed else "SAFE")
            Pin.levels[ARM_SWITCH_PIN] = 0 if armed else 1
        self.at(ms, flip)

    def heartbeats(self, start, end, session, every=1000):
        for ms in range(start, end, every):
            self.host(ms, H_BRIDGE.CMD_HOST_HEARTBEAT, session)

    def tick(self):
        self.events.sort()
        while self.events and self.events[0][0] <= now():
            self.events.pop(0)[2]()
        if not self.events:
            log("END")
            raise StopHarness()


DRIVER = Driver()


def scenario(d):
    b = H_BRIDGE
    sa, sb, sc = b"\x00\x00\x00\xa1", b"\x00\x00\x00\xb2", b"\x00\x00\x00\xc3"
    discover = P.build_discover_broadcast(0x1234)

    # Before any app session.
    for cmd, payload in ((b.CMD_PING, b""), (b.CMD_GET_INFO, b""), (b.CMD_GET_STATS, b""),
                         (b.CMD_GET_INTERFACE_STATUS, b""), (b.CMD_GET_INTERFACE_STATUS, b"\x01"),
                         (b.CMD_GET_KEEPALIVE_STATUS, b""), (b.CMD_LED_BRIGHTNESS, b""),
                         (b.CMD_LED_BRIGHTNESS, b"\x28"), (b.CMD_SEND_NFCP, discover),
                         (b.CMD_PAIR, b""), (b.CMD_PAIR, b"\x05"), (b.CMD_HOST_HEARTBEAT, b"\x01"),
                         (b.CMD_SET_NETWORK, b"\x12\x34")):
        d.host(10, cmd, payload)

    # Session A: settings and maintenance.
    d.heartbeats(100, 4600, sa)
    d.host(200, b.CMD_LED_BRIGHTNESS, b"\x28")
    d.host(200, b.CMD_LED_BRIGHTNESS, b"\x00")
    d.host(210, b.CMD_SET_NETWORK, b"\x12\x34")
    d.host(220, b.CMD_SET_NETWORK, b"\x04\xd2")
    d.host(230, b.CMD_SET_CHANNEL, b"\x55")
    d.host(240, b.CMD_SET_CHANNEL, b"\x32")
    d.host(250, b.CMD_SET_CHANNEL, b"\x5a")
    d.host(260, b.CMD_SCAN_CHANNELS, b"")
    d.host(270, b.CMD_CLEAR_STATS, b"")
    d.host(280, 0x55, b"")
    d.host(290, b.CMD_GET_INTERFACE_STATUS, b"")

    # Discovery, plain sends and replies the bridge hears.
    d.host(300, b.CMD_SEND_NFCP, discover)
    d.radio(320, lambda: module_frame(UID, P.build_discover_response(UID, 1, UID, 24, 0x20, 0x20, 0, 0x1234),
                                      msg_id=0x0101), "discover rsp")
    d.radio(325, lambda: module_frame(UID, P.build_discover_response(UID, 1, UID, 24, 0x20, 0x20, 0, 0x1234),
                                      msg_id=0x0101), "duplicate discover rsp")
    d.radio(330, lambda: module_frame(UID, P.build_discover_response(UID, 1, UID, 24, 0x20, 0x20, 0, 0x1234),
                                      network=0x0999, msg_id=0x0102), "other network")
    d.host(350, b.CMD_SEND_NFCP, P.build_info_request(UID))
    d.host(360, b.CMD_SEND_NFCP, P.build_resistance_request(UID))
    d.host(370, b.CMD_SEND_NFCP, P.build_set_group(UID, 2))
    d.host(380, b.CMD_SEND_NFCP, P.build_set_radio_channel(UID, 85))
    d.host(390, b.CMD_SEND_NFCP, P.build_group_fire(1, 1))
    d.host(395, b.CMD_SEND_NFCP, b"\xff\xfb\x01\x02")
    d.host(398, b.CMD_SEND_NFCP, P.build_keepalive_broadcast(5))

    # Reliable delivery: answered, to a uid with 0xFF bytes, and unanswered.
    d.host(400, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_info_request(UID)))
    d.radio(430, lambda: module_frame(UID, P.build_response(UID, H_NFCP.CMD_INFO_RSP, b"\x02hi")), "info rsp")
    d.host(500, b.CMD_SEND_NFCP_RELIABLE, reliable(UID_FF, P.build_ping(UID_FF, b"\xff\x01")))
    d.radio(520, lambda: module_frame(UID_FF, P.build_response(UID_FF, H_NFCP.CMD_PING, b"\xff\x01")), "ping rsp")
    d.host(600, b.CMD_SEND_NFCP_RELIABLE, reliable(0x0BADF00D, P.build_info_request(0x0BADF00D), 600))
    d.host(610, b.CMD_SEND_NFCP_RELIABLE, b"\x00\x00\x00\x01\x00")

    # Length and value checks of the master policy, and ARM with the key on SAFE.
    for ms, frame in ((650, P.build_unique_command(UID, H_NFCP.CMD_FIRE_PULSE, b"\x05")),
                      (651, P.build_set_resistance_calibration(UID, 0, [(100, 0), (300, 20)])),
                      (652, P.build_unique_command(UID, H_NFCP.CMD_SET_RESISTANCE_CAL, b"\x01\x08\x01" + b"\x00" * 4)),
                      (653, P.build_script_load(UID, 1, 0, [(0, 1), (10, 2)])),
                      (654, P.build_ota_update_begin(UID, 1, "field_main.py", 100, 7)),
                      (655, P.build_ping(UID, b"\x01" * 17)),
                      (656, P.build_resistance_request(UID, raw_adc=True)),
                      (657, P.build_unique_command(UID, H_NFCP.CMD_RESISTANCE_REQ, b"\x02")),
                      (658, P.build_set_radio_channel(UID, 50)),
                      (659, P.build_script_heartbeat_broadcast(1, 5)),
                      (660, P.build_time_sync_broadcast(1234)),
                      (661, P.build_arm_broadcast())):
        d.host(ms, b.CMD_SEND_NFCP, frame)
    d.host(700, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_arm_unique(UID)))

    # ARM key, arming, firing, refusals while armed, disarm by ACK.
    d.switch(800, True)
    d.host(900, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_arm_unique(UID)))
    d.radio(920, lambda: module_frame(UID, P.build_ack(UID, H_NFCP.CMD_ARM)), "arm ack")
    d.host(950, b.CMD_SEND_NFCP, P.build_unique_fire(UID, 0x5))
    d.host(955, b.CMD_SEND_NFCP, P.build_unique_fire(0x0BADF00D, 0x5))
    d.host(960, b.CMD_SEND_NFCP, P.build_set_group(UID, 3))
    d.host(965, b.CMD_SEND_NFCP, P.build_unique_command(UID, H_NFCP.CMD_FIRE_PULSE, b"\x00\x0a"))
    d.host(966, b.CMD_SEND_NFCP, P.build_unique_command(UID, H_NFCP.CMD_FIRE_PULSE))
    d.host(970, b.CMD_LED_BRIGHTNESS, b"\x30")
    d.host(975, b.CMD_PAIR, b"\x05")
    d.host(980, b.CMD_SET_CHANNEL, b"\x55")
    d.host(985, b.CMD_GET_INTERFACE_STATUS, b"")
    d.host(1500, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_disarm_unique(UID)))
    d.radio(1520, lambda: module_frame(UID, P.build_ack(UID, H_NFCP.CMD_DISARM)), "disarm ack")
    d.host(1600, b.CMD_GET_INTERFACE_STATUS, b"")
    d.switch(1700, False)
    d.switch(1750, True)
    d.host(1800, b.CMD_SEND_NFCP, P.build_unique_fire(UID, 0x1))

    # Pairing: refused sends during the window, an offer reply, early end,
    # then a window that runs out.
    d.switch(1900, False)
    d.host(2000, b.CMD_PAIR, b"\x03")
    d.host(2010, b.CMD_PAIR, b"")
    d.host(2020, b.CMD_SEND_NFCP, discover)
    d.host(2030, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_info_request(UID)))
    d.radio(2400, lambda: module_frame(UID, P.build_response(UID, H_NFCP.CMD_PAIR_RSP, b"\x04\xd2\x5a\x00\x01\x00\x01"),
                                       network=H_NFCP.PAIR_NETWORK_ID, msg_id=0x0201), "pair rsp")
    d.host(2700, b.CMD_PAIR, b"\x00")
    d.host(2710, b.CMD_PAIR, b"\x00")
    d.host(2800, b.CMD_PAIR, b"\x01")
    d.host(2810, b.CMD_PAIR, b"\x1f")

    # Armed when a new app session takes over: STOP and DISARM broadcasts,
    # keepalive held.
    d.switch(3900, True)
    d.host(4000, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_arm_unique(UID)))
    d.radio(4020, lambda: module_frame(UID, P.build_ack(UID, H_NFCP.CMD_ARM)), "arm ack")
    d.host(4100, b.CMD_SEND_NFCP, P.build_disarm_broadcast())
    d.host(4110, b.CMD_GET_INTERFACE_STATUS, b"")
    d.host(4200, b.CMD_SEND_NFCP_RELIABLE, reliable(UID, P.build_arm_unique(UID)))
    d.heartbeats(4600, 6000, sb)
    d.host(5000, b.CMD_SEND_NFCP, P.build_unique_fire(UID, 0x1))
    d.host(5100, b.CMD_GET_KEEPALIVE_STATUS, b"")
    d.switch(5200, False)

    # Session expiry, a main-loop error, recovery.
    d.host(9500, b.CMD_GET_INTERFACE_STATUS, b"")
    d.host(9510, b.CMD_SEND_NFCP, discover)
    d.at(9600, lambda: setattr(RADIO, "fail_next_recv", True))

    # Session C: the hold after the STOP has passed; a file update; reset.
    d.heartbeats(10000, 30500, sc)
    d.host(26000, b.CMD_GET_KEEPALIVE_STATUS, b"")
    content = b"# updated by the bridge trace harness\n"
    name = b"command_interface.py"
    begin = (bytes([len(name)]) + name + b.int_to_be(len(content), 4) +
             b.int_to_be(b.crc16_ccitt(content), 2))
    d.host(26100, 0x32, begin)
    d.host(26110, 0x33, b.int_to_be(0, 4) + content)
    d.host(26120, 0x34, b"")
    d.host(26130, 0x32, b"\x03abc\x00\x00\x00\x01\x00\x00")
    d.host(26140, 0x33, b"\x00\x00\x00\x00x")
    d.host(26150, 0x34, b"")
    d.host(26200, b.CMD_SEND_NFCP, P.build_unique_fire(UID, 0x1))
    # Another session takes over with nothing armed: changes wait for the
    # STOP broadcasts.
    sd = b"\x00\x00\x00\xd4"
    d.host(27500, b.CMD_HOST_HEARTBEAT, sd)
    d.host(27505, b.CMD_LED_BRIGHTNESS, b"\x20")
    d.host(27510, b.CMD_PAIR, b"\x02")
    d.host(27515, b.CMD_SET_CHANNEL, b"\x55")
    d.host(27700, b.CMD_LED_BRIGHTNESS, b"\x21")
    d.host(30000, b.CMD_RESET, b"")
    d.at(31000, lambda: None)


def config_module(master):
    config = types.ModuleType("nrf_bridge_config")
    config.NETWORK_ID = NETWORK
    config.RADIO_CHANNEL = 90
    config.RADIO_TX_POWER = 8
    config.ARM_SWITCH_PIN = ARM_SWITCH_PIN
    config.STATUS_LED_PIN = 8
    config.STATUS_LED_COUNT = 4
    config.STATUS_LED_ORDER = "RGB"
    config.STATUS_LED_TIMING_NS = (150, 1100, 550, 700)
    config.STATUS_LED_POWER_PIN = 13
    config.STATUS_LED_POWER_ON_VALUE = 1
    config.STATUS_LED_BRIGHTNESS = 12
    if master:
        config.MASTER_ENABLED = True
    return config


def install_fakes(master):
    sys.modules["time"] = fake_time
    sys.modules["utime"] = fake_time
    sys.modules["machine"] = fake_machine
    sys.modules["nrf_radio"] = RADIO
    sys.modules["usb_bridge_transport"] = fake_transport
    sys.modules["nrf_bridge_config"] = config_module(master)
    with io.open("nrf_bridge_config.py", "w", encoding="utf-8") as handle:
        handle.write("MASTER_ENABLED = %s\n" % master)


def run_bridge(src_dir):
    sys.path[:0] = [src_dir, MAIN_DIR]
    spec = importlib.util.spec_from_file_location(
        "bridge_under_test", os.path.join(src_dir, "nrf_usb_monitor_main.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("master", "monitor"), default="master")
    parser.add_argument("--src", default=MAIN_DIR)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    work = tempfile.mkdtemp(prefix="bridgetrace-")
    cwd = os.getcwd()
    os.chdir(work)
    install_fakes(args.mode == "master")
    scenario(DRIVER)
    real_stdout = sys.stdout
    sys.stdout = Printer()
    try:
        run_bridge(src)
    except StopHarness:
        pass
    except BaseException as error:
        log("HARNESS_ERROR", repr(error))
        import traceback
        for line in traceback.format_exc().splitlines():
            log("TB", line)
    finally:
        sys.stdout = real_stdout
        os.chdir(cwd)
        shutil.rmtree(work, ignore_errors=True)
    with io.open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(TRACE) + "\n")
    print("%s: %d trace lines -> %s" % (args.mode, len(TRACE), out))
    errors = [line for line in TRACE if "HARNESS_ERROR" in line or " TB " in line]
    for line in errors[:30]:
        print(line)


if __name__ == "__main__":
    main()
