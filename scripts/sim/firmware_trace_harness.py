"""Run the field or dummy module firmware on a PC against fake hardware.

The firmware is driven through a fixed scenario (discovery, settings, arming,
script upload and run, step mode, failsafes, LCD menu) and everything it does
is written to a trace: radio transmissions, output changes, LCD lines and
console prints. Comparing traces from two versions of the firmware shows
whether a refactor changed behaviour.

    py -3 scripts/sim/firmware_trace_harness.py --fw dummy --out dummy.trace
    py -3 scripts/sim/firmware_trace_harness.py --fw field --src old_main_dir --out old.trace

--src names the folder holding field_main.py / nrf_dummy_main.py; the other
modules always come from main/.
"""

from __future__ import print_function

import argparse
import importlib.util
import io
import os
import random
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_DIR = os.path.join(ROOT, "main")

TRACE = []
_clock = {"ms": 1000}
TICKS_PERIOD = 1 << 30


def now():
    return _clock["ms"]


def log(kind, *parts):
    TRACE.append("%8d %s %s" % (now(), kind, " ".join(str(p) for p in parts)))


# --- fake time --------------------------------------------------------------
fake_time = types.ModuleType("time")
fake_time.ticks_ms = lambda: _clock["ms"] % TICKS_PERIOD
fake_time.ticks_us = lambda: (_clock["ms"] * 1000) % TICKS_PERIOD
fake_time.ticks_add = lambda a, b: (a + b) % TICKS_PERIOD


def _ticks_diff(a, b):
    half = TICKS_PERIOD // 2
    return ((a - b + half) % TICKS_PERIOD) - half


fake_time.ticks_diff = _ticks_diff


def _sleep_ms(ms):
    _clock["ms"] += int(ms)


fake_time.sleep_ms = _sleep_ms
fake_time.sleep_us = lambda us: None
fake_time.sleep = lambda s: _sleep_ms(int(s * 1000))
fake_time.time = lambda: _clock["ms"] // 1000


# --- fake machine -----------------------------------------------------------
class Pin(object):
    IN = 0
    OUT = 1
    PULL_UP = 1
    PULL_DOWN = 2
    OPEN_DRAIN = 3
    levels = {}

    def __init__(self, pin_id, mode=None, pull=None, value=None):
        self.id = pin_id
        self.mode = mode
        if value is not None:
            self.value(value)
        elif pin_id not in Pin.levels:
            Pin.levels[pin_id] = 1

    def init(self, *args, **kwargs):
        pass

    def value(self, v=None):
        if v is None:
            return Pin.levels.get(self.id, 1)
        v = 1 if v else 0
        if Pin.levels.get(self.id) != v and self.id in WATCHED_PINS:
            log("PIN", "P%s=%d" % (self.id, v))
        Pin.levels[self.id] = v
        return None

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def __call__(self, v=None):
        return self.value(v)


WATCHED_PINS = set()


class ADC(object):
    reading = 40000

    def __init__(self, pin, *args, **kwargs):
        self.pin = pin

    def read_u16(self):
        return ADC.reading


class I2C(object):
    def __init__(self, *args, **kwargs):
        pass

    def scan(self):
        return [0x27]

    def writeto(self, *args, **kwargs):
        return None


fake_machine = types.ModuleType("machine")
fake_machine.Pin = Pin
fake_machine.ADC = ADC
fake_machine.I2C = I2C
fake_machine.unique_id = lambda: b"\x01\x02\xd9\x6e\x30\xdd"


def _reset():
    log("RESET")


fake_machine.reset = _reset


# --- fake radio ---------------------------------------------------------------
fake_radio = types.ModuleType("nrf_radio")


def _radio_init(channel=None, tx_power=None, address=None):
    log("RADIO_INIT", "ch=%s power=%s addr=%s" % (channel, tx_power, address))


def _radio_send(payload, cca_ms=None, busy_dbm=None, backoff_ms=None):
    log("TX", bytes(payload).hex())
    return True


fake_radio.init = _radio_init
fake_radio.send = _radio_send
fake_radio.recv = lambda timeout=0: None
fake_radio.last_rssi = lambda: -60
fake_radio.device_id32 = lambda: 0xD96E30DD

fake_urandom = types.ModuleType("urandom")
_rng = random.Random(1234)
fake_urandom.getrandbits = lambda bits: _rng.getrandbits(bits)


# --- fake LCD -----------------------------------------------------------------
class FakeLcd(object):
    def __init__(self, i2c, address, cols=16, rows=2, backlight=True, mapping="upper"):
        self.last = None

    @staticmethod
    def find_address(i2c, preferred=None):
        return 0x27

    def show_lines(self, a, b="", max_rows=None):
        lines = (a, b)
        if lines != self.last:
            log("LCD", repr(a), repr(b))
        self.last = lines
        return True


fake_lcd_module = types.ModuleType("lcd1602_i2c")
fake_lcd_module.I2cLcd1602 = FakeLcd


class Printer(io.StringIO):
    def write(self, text):
        for line in text.splitlines():
            if line.strip():
                log("PRINT", line)
        return len(text)


def install_fakes(fw):
    sys.modules["time"] = fake_time
    sys.modules["utime"] = fake_time
    sys.modules["machine"] = fake_machine
    sys.modules["nrf_radio"] = fake_radio
    sys.modules["urandom"] = fake_urandom
    sys.modules["lcd1602_i2c"] = fake_lcd_module
    config = types.ModuleType("nrf_node_config")
    if fw == "field":
        config.ARM_CONTROL_PIN = 5
        config.OK_FWD_BUTTON_PIN = 40
        config.BACK_BUTTON_PIN = 41
        config.CUE_COUNT = 16
        config.LOG_FIRE_EVENTS = True
    sys.modules["nrf_node_config"] = config


def load_firmware(fw, src_dir):
    entry = "field_main.py" if fw == "field" else "nrf_dummy_main.py"
    sys.path[:0] = [src_dir, MAIN_DIR]
    spec = importlib.util.spec_from_file_location("fw_under_test", os.path.join(src_dir, entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules["fw_under_test"] = module
    spec.loader.exec_module(module)
    return module


MASTER_ORIGIN = 0x0B0B0B0B   # mesh origin used for frames from the simulated bridge


class Driver(object):
    def __init__(self, fw_name, fw):
        self.name = fw_name
        self.fw = fw
        import nfcp
        self.nfcp = nfcp
        self.master = nfcp.NFCP(node_id=0x00)
        self.seq = 10
        self.net = fw.NETWORK_ID

    @property
    def uid(self):
        return self.fw.UNIQUE_ID

    def setup(self):
        fw = self.fw
        if os.path.exists("field_service.flag"):
            os.remove("field_service.flag")
        if hasattr(fw, "setup"):
            return fw.setup()
        fw.init_lcd_ui()
        if self.name == "field":
            fw.set_armed_outputs(False)
            fw.run_startup_self_tests()
        return fw.init_radio()

    def loop_once(self):
        fw = self.fw
        if hasattr(fw, "loop_once"):
            fw.loop_once()
            return
        fw.module_ota.poll_timeout()
        if self.name == "field":
            fw.poll_fire_outputs()
        fw.run_script_scheduler()
        fw.check_master_link_failsafe()
        fw.service_lcd_ui()

    def run(self, ms, step=10):
        end = now() + ms
        while now() < end:
            self.loop_once()
            _clock["ms"] += step

    def send(self, frame, label, net=None, seq=None):
        """Deliver an NFCP frame as the bridge would send it: a mesh frame.
        Passing an earlier return value as seq resends the same msg_id."""
        import mesh
        if seq is None:
            self.seq = (self.seq + 1) & 0xFFFF or 1
            seq = self.seq
        payload = mesh.mesh_build(self.net if net is None else net, MASTER_ORIGIN, seq, frame)
        log("RX", label)
        self.fw.handle_radio_payload(payload)
        self.loop_once()
        return seq

    def press(self, pin, label):
        log("BUTTON", label)
        Pin.levels[pin] = 0
        self.run(150)
        Pin.levels[pin] = 1
        self.run(150)


def scenario(d):
    m = d.master
    n = d.nfcp
    d.setup()
    d.run(500)

    d.send(m.build_discover_broadcast(nonce=0x1234), "discover")
    d.send(m.build_info_request(d.uid), "info")
    d.send(m.build_ping(d.uid, b"\x01\x02"), "ping")
    d.send(m.build_unique_command(d.uid + 1, n.CMD_PING, b"\x09"), "ping other uid")
    d.send(m.build_output_status_request(d.uid), "output status")
    d.send(m.build_keepalive_broadcast(), "keepalive")

    # Settings while safe.
    d.send(m.build_set_group(d.uid, 0x0042), "set group 0x42")
    d.send(m.build_unique_command(d.uid, n.CMD_SET_GROUP, b"\x01"), "set group bad length")
    d.send(m.build_set_radio_channel(d.uid, 50), "set channel invalid")
    d.send(m.build_set_radio_channel(d.uid, 80), "set channel 80")
    d.send(m.build_set_network(d.uid, 0x2222), "set network 0x2222")
    d.send(m.build_info_request(d.uid), "info on old network (ignored)")
    d.net = 0x2222
    d.send(m.build_info_request(d.uid), "info on new network")
    old = d.uid
    d.send(m.build_set_unique(d.uid, 0x0BADF00D), "set unique")
    d.send(m.build_info_request(old), "info old uid (ignored)")
    d.send(m.build_info_request(d.uid), "info new uid")

    # Group addressing and duplicate suppression.
    seq = d.send(m.build_unique_command(d.uid, n.CMD_PING, b"\x05"), "ping")
    d.send(m.build_unique_command(d.uid, n.CMD_PING, b"\x05"), "ping duplicate", seq=seq)

    # Fire while safe is refused.
    d.send(m.build_unique_fire(d.uid, 0x3), "fire safe")

    # Arm, fire, disarm.
    d.send(m.build_arm_unique(d.uid), "arm")
    d.send(m.build_set_group(d.uid, 7), "set group while armed")
    d.send(m.build_unique_fire(d.uid, 0x5), "fire 0x5")
    d.run(100)
    d.send(m.build_output_status_request(d.uid), "output status after fire")
    d.send(m.build_group_fire(0x0042, 0x8), "group fire 0x8")
    d.run(100)
    d.send(m.build_disarm_unique(d.uid), "disarm")

    # Script upload.
    sid = 0x5A5A
    d.send(m.build_script_clear(d.uid, sid), "script clear")
    d.send(m.build_script_load(d.uid, sid, 0, [(500, 0x1), (1000, 0x2), (1000, 0x4)]), "script load 0")
    d.send(m.build_script_load(d.uid, sid, 1, [(1500, 0x8), (0, 0x0)]), "script load 1")
    d.send(m.build_unique_command(d.uid, n.CMD_SCRIPT_LOAD, b"\x5a"), "script load bad")
    d.send(m.build_info_request(d.uid), "info with script")
    d.send(m.build_script_start(d.uid, sid, 300), "start while safe")

    # Script run with time sync and heartbeats.
    d.send(m.build_arm_unique(d.uid), "arm")
    d.send(m.build_script_start(d.uid, 0x1111, 300), "start wrong id")
    master_ms = 5000
    d.send(m.build_time_sync_broadcast(master_ms), "time sync")
    d.send(m.build_script_start(d.uid, sid, 300, 0, master_ms + 400), "start synced")
    for i in range(12):
        d.run(150)
        d.send(m.build_script_heartbeat_broadcast(sid, i * 150), "heartbeat")
        if i == 5:
            d.send(m.build_output_status_request(d.uid), "output status mid run")
    d.send(m.build_info_request(d.uid), "info after run")

    # Start at an offset, then stop.
    d.send(m.build_script_start(d.uid, sid, 200, 900), "start offset 900")
    d.run(250)
    d.send(m.build_script_heartbeat(d.uid, sid, 0), "heartbeat unique")
    d.run(200)
    d.send(m.build_script_stop(d.uid, sid), "stop")
    d.run(1500)

    # Heartbeat timeout.
    d.send(m.build_script_start(d.uid, sid, 100), "start no heartbeats")
    d.run(2500)
    d.send(m.build_info_request(d.uid), "info after hb timeout")

    # Step mode.
    d.send(m.build_script_step(d.uid, sid, 1000), "step 1000")
    d.send(m.build_script_step(d.uid, sid, 1234), "step missing")
    d.send(m.build_script_step(d.uid, 0x1111, 500), "step wrong id")
    d.run(100)

    # Disarm stops a running script.
    d.send(m.build_script_start(d.uid, sid, 100), "start then disarm")
    d.run(300)
    d.send(m.build_disarm_broadcast(), "disarm broadcast")
    d.run(800)
    d.send(m.build_script_step(d.uid, sid, 500), "step while safe")

    # Master link failsafe.
    d.send(m.build_arm_unique(d.uid), "arm")
    d.run(16000, step=50)
    d.send(m.build_info_request(d.uid), "info after failsafe")

    # OTA refused while armed / handled while safe.
    d.send(m.build_arm_unique(d.uid), "arm")
    d.send(m.build_ota_update_begin(d.uid, 7, "/x.py", 4, 0x1234), "ota begin armed")
    d.send(m.build_disarm_unique(d.uid), "disarm")
    d.send(m.build_ota_update_abort(d.uid, 7), "ota abort")
    d.send(m.build_ota_update_reboot(d.uid), "ota reboot")

    # LCD menu.
    if d.name == "field":
        up, down, ok, back = 33, 34, 40, 41
    else:
        up, down, ok, back = 45, 47, 43, 10
    d.run(400)
    d.press(ok, "ok -> menu")
    d.press(ok, "ok -> system info")
    d.press(back, "back")
    d.press(down, "down")
    d.press(ok, "ok -> continuity")
    d.press(ok, "ok (field: scan)")
    d.press(down, "down")
    d.press(down, "down")
    d.press(up, "up")
    d.press(back, "back")
    d.press(down, "down")
    d.press(ok, "ok -> script")
    d.press(back, "back")
    d.press(down, "down")
    d.press(ok, "ok -> set group")
    d.press(up, "up")
    d.press(up, "up")
    d.press(ok, "ok save")
    d.press(down, "down")
    d.press(ok, "ok -> set network")
    d.press(back, "back")
    d.press(down, "down")
    d.press(ok, "ok -> set channel")
    d.press(up, "up")
    d.press(ok, "ok save channel")
    d.send(m.build_arm_unique(d.uid), "arm")
    d.press(up, "up")
    d.press(up, "up")
    d.press(ok, "ok -> set group armed")
    d.press(down, "down")
    d.press(ok, "ok save while armed")
    d.press(back, "back")
    d.press(back, "back")
    d.send(m.build_disarm_unique(d.uid), "disarm")
    d.run(500)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fw", choices=("field", "dummy"), required=True)
    parser.add_argument("--src", default=MAIN_DIR)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)
    work = tempfile.mkdtemp(prefix="fwtrace-")
    os.chdir(work)
    install_fakes(args.fw)
    if args.fw == "field":
        # Service mode stops field_main from entering its endless main loop on import.
        open("field_service.flag", "w").close()
        WATCHED_PINS.update((5, 10, 11))
    real_stdout = sys.stdout
    sys.stdout = Printer()
    try:
        fw = load_firmware(args.fw, src)
        scenario(Driver(args.fw, fw))
    except BaseException as error:
        log("HARNESS_ERROR", repr(error))
        import traceback
        for line in traceback.format_exc().splitlines():
            log("TB", line)
    finally:
        sys.stdout = real_stdout
        shutil.rmtree(work, ignore_errors=True)
    with io.open(out, "w", encoding="utf-8") as handle:
        handle.write("\n".join(TRACE) + "\n")
    print("%s: %d trace lines -> %s" % (args.fw, len(TRACE), out))
    errors = [line for line in TRACE if "HARNESS_ERROR" in line or " TB " in line]
    for line in errors[:30]:
        print(line)


if __name__ == "__main__":
    main()
