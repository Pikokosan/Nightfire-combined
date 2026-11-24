"""Bench test 3b (audio_box/design.md): a field or dummy module follows the
audio box in a chase show, on real boards.

ARMS A MODULE. Disconnect every igniter first: the test refuses to arm if
any cue of the module reads closed, and asks for the bridge's ARM key.

Boards: the USB bridge (usb-master-0.4.7+), the audio box, and one module
(field-module 0.3.3+ or nrf-dummy 0.2.3+), all on USB so their prints can
be read with PC timestamps.

The show: cues at 0.5, 1.5, 3, 4.5, 6 and 9 s of the track. The box plays
from 0; at about 3.4 s it is told to play from 4.9 s (a jump that must
skip the 4.5 s cue); at about 7 s it is stopped (so the 9 s cue never
fires and the module stops on the box's `stopped`). Each cue's FIRE print
on the module is compared with the box's `AUDIO play` print plus the cue's
place in the track. That USB timing is printed but not judged: the box's
own prints jitter by up to ~12 ms. Timing passes or fails on the module's
own log (field-module 0.3.4+): every SHOW_TIME within 5 ms of its clock,
and no cue fired more than 10 ms past its own cue time.
--shows runs the show several times on one turn of the ARM key (the module
stays armed between shows, as after an operator stop) and sums them up.

Whatever happens, the module is disarmed at the end (broadcast STOP and
DISARM, then a confirmed unique DISARM) and the box is stopped.

Run: py -3 scripts/bench/test_audio_box_follow.py --port COM12 --box-port COM18 --module-port COM16
"""
import argparse
import re
import sys
import threading
import time
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "main"), str(ROOT / "scripts" / "bench")]
import bridge_radio  # noqa: E402
import nfcp  # noqa: E402
from test_audio_box_bench import Bridge, ack, check, status, RESULTS  # noqa: E402

TRACK = 7
SCRIPT_ID = 0x4D53
CUES = [(500, 0x1), (1500, 0x2), (3000, 0x4), (4500, 0x8), (6000, 0x10), (9000, 0x20)]
JUMP_AT_S = 3.4
JUMP_TO_MS = 4900
STOP_AT_S = 7.0
GOAL_MS = 10.0          # the design target, reported from the USB prints only
RADIO_GOAL_MS = 5       # SHOW_TIME error against the module's clock (module log)
LATE_GOAL_MS = 10       # cue fired after its own time (module loop)
HEARTBEAT_EVERY_S = 0.2
KEY_WAIT_S = 180


class SerialLog:
    """Lines a board prints, with the PC time each arrived."""

    def __init__(self, port):
        self.serial = serial.Serial(port, 115200, timeout=0.002)
        self.lines = []
        self.alive = True
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        buf = b""
        while self.alive:
            data = self.serial.read(256)
            if not data:
                continue
            t = time.perf_counter()
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self.lines.append((t, line.decode("utf-8", "replace").strip()))

    def find(self, pattern, since=0.0):
        for t, line in self.lines:
            if t >= since and re.search(pattern, line):
                return t, line
        return None

    def close(self):
        self.alive = False
        self.thread.join(1)
        self.serial.close()


def beat(bridge, master, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        bridge.write(bridge_radio.CMD_SEND_NFCP, master.build_script_heartbeat_broadcast(SCRIPT_ID, 0))
        bridge.pump(min(HEARTBEAT_EVERY_S, max(0.0, end - time.monotonic())))


def key_ready(bridge):
    """True when the bridge's ARM key is on ARM and not latched (a key left
    on ARM at connect must go through SAFE first)."""
    seq = bridge.write(bridge_radio.CMD_GET_INTERFACE_STATUS)
    end = time.monotonic() + 1.5
    while time.monotonic() < end:
        bridge.pump(0.05)
        reply = bridge.replies.get(seq)
        if reply is not None:
            d = bytes(reply.payload)
            return len(d) >= 6 and d[2] == 2 and not d[5] & 32
    return False


def discover(bridge, master):
    since = time.monotonic()
    bridge.write(bridge_radio.CMD_SEND_NFCP, master.build_discover_broadcast(0x3B3B))
    bridge.pump(1.5)
    found = {}
    for t, pkt, rssi in bridge.heard:
        if t >= since and pkt["cmd"] == nfcp.CMD_DISCOVER_RSP and len(pkt["data"]) >= 10:
            d = bytes(pkt["data"])
            found[pkt["src"]] = (d[7], d[6], rssi)
    return found


def info(bridge, master, uid):
    pkt = bridge.request(uid, master.build_info_request(uid), nfcp.CMD_INFO_RSP)
    fields, d = [], bytes(pkt["data"]) if pkt else b""
    while d:
        fields.append(d[1:1 + d[0]].decode("utf-8", "replace"))
        d = d[1 + d[0]:]
    return fields


def output_flags(bridge, master, uid):
    pkt = bridge.request(uid, master.build_output_status_request(uid), nfcp.CMD_OUTPUT_STATUS_RSP)
    if pkt is None or pkt["cmd"] != nfcp.CMD_OUTPUT_STATUS_RSP:
        return None
    return bytes(pkt["data"])


def all_cues_open(bridge, master, uid):
    pkt = bridge.request(uid, master.build_unique_command(uid, nfcp.CMD_RESISTANCE_REQ), nfcp.CMD_RESISTANCE_RSP, 6)
    if pkt is None or pkt["cmd"] != nfcp.CMD_RESISTANCE_RSP:
        return False, "no resistance reply"
    d = bytes(pkt["data"])
    values = [int.from_bytes(d[4 + 2 * i:6 + 2 * i], "big") for i in range(d[2])]
    closed = ["cue %d %.1f ohm" % (i + 1, v / 10.0) for i, v in enumerate(values) if v < 0xFFFE]
    return not closed, ", ".join(closed) or "%d cues open" % len(values)


def make_safe(bridge, master, module, box):
    """Stop the show and disarm, whatever state things are in."""
    for unused in range(3):
        bridge.write(bridge_radio.CMD_SEND_NFCP, master.build_script_stop_broadcast(SCRIPT_ID))
        bridge.write(bridge_radio.CMD_SEND_NFCP, master.build_broadcast(nfcp.CMD_DISARM))
        bridge.pump(0.1)
    if box is not None:
        ack(bridge, box, master.build_audio_stop(box, TRACK), nfcp.CMD_AUDIO_STOP)
    confirmed = False
    for unused in range(3):
        if ack(bridge, module, master.build_disarm_unique(module), nfcp.CMD_DISARM) == ("ACK", 0):
            confirmed = True
            break
    flags = output_flags(bridge, master, module)
    safe = confirmed and flags is not None and not flags[1] & 0x09 and flags[3:11] == bytes(8)
    check("module disarmed and confirmed", safe, flags.hex() if flags else "no output status")
    return safe


def upload_and_arm(bridge, master, module):
    """The bridge takes script uploads only while nothing is armed, so
    upload first, then arm (as the app does). The module stays armed
    across the shows: a stop leaves it armed with its script loaded."""
    ok = (ack(bridge, module, master.build_script_clear(module, SCRIPT_ID), nfcp.CMD_SCRIPT_CLEAR) == ("ACK", 0) and
          ack(bridge, module, master.build_script_load(module, SCRIPT_ID, 0, CUES[:3]), nfcp.CMD_SCRIPT_LOAD) == ("ACK", 0) and
          ack(bridge, module, master.build_script_load(module, SCRIPT_ID, 1, CUES[3:]), nfcp.CMD_SCRIPT_LOAD) == ("ACK", 0))
    check("script upload", ok)
    if not ok:
        return False
    check("ARM module", ack(bridge, module, master.build_arm_unique(module), nfcp.CMD_ARM) == ("ACK", 0))
    flags = output_flags(bridge, master, module)
    armed = flags is not None and bool(flags[1] & 0x01)
    check("module reports armed", armed, flags.hex() if flags else "")
    return armed


def one_show(n, bridge, master, module, box, box_log, module_log):
    """One chase show. Prints only failures and a summary line; returns
    the summary (or None if the show didn't get as far as the cues)."""
    fails = []

    def expect(label, ok, detail=""):
        RESULTS.append(bool(ok))
        if not ok:
            fails.append(label)
            print("FAIL  show %d: %-34s %s" % (n, label, detail), flush=True)

    started = time.perf_counter()
    code = ack(bridge, module, master.build_script_start_chase(module, SCRIPT_ID, box, TRACK), nfcp.CMD_SCRIPT_START)
    expect("chase START answered ACK_CHASE", code == ("ACK", nfcp.ACK_CHASE), str(code))
    if code != ("ACK", nfcp.ACK_CHASE):
        return None
    beat(bridge, master, 1.0)
    expect("nothing fires before the music", module_log.find(r"SCRIPT FIRE", started) is None)

    expect("AUDIO_PLAY", ack(bridge, box, master.build_audio_play(box, TRACK, 0), nfcp.CMD_AUDIO_PLAY) == ("ACK", 0))
    beat(bridge, master, JUMP_AT_S - 0.15)
    jump_sent = time.perf_counter()
    expect("AUDIO_PLAY jump", ack(bridge, box, master.build_audio_play(box, TRACK, JUMP_TO_MS),
                                  nfcp.CMD_AUDIO_PLAY) == ("ACK", 0))
    beat(bridge, master, STOP_AT_S - JUMP_AT_S)
    expect("AUDIO_STOP", ack(bridge, box, master.build_audio_stop(box, TRACK), nfcp.CMD_AUDIO_STOP) == ("ACK", 0))
    beat(bridge, master, 1.0)

    play = box_log.find(r"^AUDIO play track=%d from=0 " % TRACK, started)
    jump = box_log.find(r"^AUDIO play track=%d from=%d " % (TRACK, JUMP_TO_MS), jump_sent - 1.0)
    stop = box_log.find(r"^AUDIO stop track=%d pos=(\d+)" % TRACK, jump_sent)
    expect("box printed play and jump", play is not None and jump is not None)
    if play is None or jump is None:
        return None
    errors, lates = [], []
    for event_ms, mask in CUES:
        hit = module_log.find(r"^SCRIPT FIRE mask=0x0*%X event=%d " % (mask, event_ms), started)
        label = "cue @%d ms" % event_ms
        if event_ms == 4500:
            expect(label + " skipped by the jump", hit is None, hit[1] if hit else "")
            continue
        if event_ms == 9000:
            expect(label + " never fires (stopped)", hit is None, hit[1] if hit else "")
            continue
        target = (play[0] + event_ms / 1000.0 if event_ms < JUMP_TO_MS
                  else jump[0] + (event_ms - JUMP_TO_MS) / 1000.0)
        if hit is None:
            expect(label, False, "did not fire")
            continue
        error = (hit[0] - target) * 1000.0
        m = re.search(r"event=(\d+) elapsed=(\d+)", hit[1])
        late = int(m.group(2)) - int(m.group(1)) if m else 0
        errors.append(error)
        lates.append(late)
        # USB print timing is information only: the box's own prints jitter
        # by up to ~12 ms against its clock. The pass/fail is the module's log.
    expect("module stops on the box's stop", module_log.find(r"SCRIPT stopped - audio stopped", started) is not None)
    # The box's own prints: jump print to stop print on the PC against the
    # positions the box printed, i.e. how much the box's USB latency moved.
    box_usb = None
    if stop is not None:
        pos = int(re.search(r"pos=(\d+)", stop[1]).group(1))
        box_usb = (stop[0] - jump[0]) * 1000.0 - (pos - JUMP_TO_MS)
    radio = chase_log(module_log, started)
    expect("module logged its SHOW_TIME (field 0.3.4+)", radio is not None)
    if radio:
        worst = max(abs(e) for e in radio["err"]) if radio["err"] else 0
        expect("every SHOW_TIME within %d ms of the module's clock" % RADIO_GOAL_MS, worst <= RADIO_GOAL_MS,
               "worst %d ms" % worst)
    expect("no cue more than %d ms past the module's own cue time" % LATE_GOAL_MS,
           all(x <= LATE_GOAL_MS for x in lates), ",".join(str(x) for x in lates))
    summary = {"errors": errors, "lates": lates, "box_usb": box_usb, "fails": fails, "radio": radio}
    print("show %2d: cues %s ms vs box | module late %s ms | box USB drift %s ms | %s" % (
        n, " ".join("%+5.1f" % e for e in errors), ",".join(str(x) for x in lates),
        "%+.1f" % box_usb if box_usb is not None else "?", "ok" if not fails else "FAIL"), flush=True)
    if radio:
        print("         radio (module log): %d messages, error %s ms, age %d-%d ms, handled <= %d ms after arrival" % (
            len(radio["err"]), " ".join("%+d" % e for e in radio["err"]), min(radio["age"]), max(radio["age"]),
            max(radio["handled"])), flush=True)
    return summary


def chase_log(module_log, since):
    """The module's SHOW_TIME log (field-module 0.3.4+): each accepted
    message's error against the module's clock before correcting, its mesh
    age and handling delay. Locks and jumps (L, J) are left out of err."""
    out = {}
    for t, line in module_log.lines:
        if t >= since and line.startswith("SCRIPT CHASE log "):
            key, value = line.rsplit(" ", 1)[1].split("=", 1)
            out[key] = value.split(",")
    if "err" not in out:
        return None
    return {"err": [int(e) for e in out["err"] if e not in ("L", "J")],
            "age": [int(a) for a in out.get("age", [])],
            "handled": [int(h) for h in out.get("handled", [])]}


def report(shows):
    done = [s for s in shows if s and s["errors"]]
    if not done:
        return
    means = [sum(s["errors"]) / len(s["errors"]) for s in done]
    spreads = [max(s["errors"]) - min(s["errors"]) for s in done]
    worst = max(abs(e) for s in done for e in s["errors"])
    late = max(x for s in done for x in s["lates"])
    good = sum(1 for s in done if all(abs(e) <= GOAL_MS for e in s["errors"]))
    print("%d shows: %d with every cue within %.0f ms. Per-show mean offset %+.1f to %+.1f ms; "
          "spread inside a show at most %.1f ms; worst cue %.1f ms; module at most %d ms past its own cue time." % (
              len(done), good, GOAL_MS, min(means), max(means), max(spreads), worst, late))
    errs = sorted(e for s in done if s.get("radio") for e in s["radio"]["err"])
    if errs:
        ages = [a for s in done if s.get("radio") for a in s["radio"]["age"]]
        handled = [h for s in done if s.get("radio") for h in s["radio"]["handled"]]
        big = [e for e in errs if abs(e) > 2]
        print("Radio, from the module's log: %d SHOW_TIME messages; error median %+d ms, 5-95%% %+d to %+d, "
              "min %+d, max %+d; %d over 2 ms; age %d-%d ms; handled up to %d ms after arrival." % (
                  len(errs), errs[len(errs) // 2], errs[len(errs) // 20], errs[len(errs) * 19 // 20],
                  errs[0], errs[-1], len(big), min(ages), max(ages), max(handled)))


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--port", default="COM12", help="USB bridge")
    args.add_argument("--box-port", default="COM18")
    args.add_argument("--module-port", default="COM16")
    args.add_argument("--shows", type=int, default=10, help="shows to run on one turn of the ARM key")
    args.add_argument("--log", help="write both boards' prints (PC time, board, line) to this file")
    options = args.parse_args()
    master = nfcp.NFCP(1)

    box_log = SerialLog(options.box_port)
    module_log = SerialLog(options.module_port)
    module = box = None
    try:
        with serial.Serial(options.port, 115200, timeout=0.01, write_timeout=2) as port:
            bridge = Bridge(port)
            bridge.pump(1.0)
            found = discover(bridge, master)
            boxes = [u for u, v in found.items() if v[0] == 0x30]
            modules = [u for u, v in found.items() if v[0] in (0x10, 0x20)]
            check("found the box and one module", len(boxes) == 1 and len(modules) == 1,
                  ", ".join("0x%08X type 0x%02X" % (u, v[0]) for u, v in found.items()))
            if len(boxes) != 1 or len(modules) != 1:
                return 1
            box, module = boxes[0], modules[0]
            fields = info(bridge, master, module)
            version = tuple(int(x) for x in fields[1].split(".")) if len(fields) > 1 else ()
            check("module firmware supports chase",
                  (fields[0] == "field-module" and version >= (0, 3, 3)) or
                  (fields[0] == "nrf-dummy" and version >= (0, 2, 3)), " ".join(fields[:2]))
            safe, detail = all_cues_open(bridge, master, module)
            check("every cue reads open (no igniters)", safe, detail)
            if not safe:
                print("Not arming: a cue reads closed. Disconnect it and run again.")
                return 1

            ready = key_ready(bridge)
            if not ready:
                print(">>> Turn the bridge's ARM key to ARM now (if it is already on ARM, turn it to SAFE"
                      " and back). Waiting up to %d s." % KEY_WAIT_S, flush=True)
            end = time.monotonic() + KEY_WAIT_S
            while not ready and time.monotonic() < end:
                ready = key_ready(bridge)
                bridge.pump(0.3)
            check("ARM key on", ready)
            if not ready:
                return 1
            shows = []
            try:
                if upload_and_arm(bridge, master, module):
                    for n in range(1, options.shows + 1):
                        shows.append(one_show(n, bridge, master, module, box, box_log, module_log))
                        if not key_ready(bridge):
                            print("ARM key left ARM: stopping after show %d" % n)
                            break
                        beat(bridge, master, 1.0)
            finally:
                make_safe(bridge, master, module, box)
            report(shows)
            print(">>> Turn the bridge's ARM key back to SAFE.")
    finally:
        box_log.close()
        module_log.close()
        if options.log:
            with open(options.log, "w", encoding="utf-8") as out:
                for name, log in (("box", box_log), ("module", module_log)):
                    for t, line in log.lines:
                        print("%.4f %-6s %s" % (t, name, line), file=out)
            print("Logs saved to", options.log)
    print("%d of %d passed" % (sum(bool(r) for r in RESULTS), len(RESULTS)))
    return 0 if all(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
