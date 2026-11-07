"""Calibrate the mesh simulator's CPU model against a real module.

The same receive workloads (real nfcp.py, module_link.py and bridge_radio.py
code) are:
1. timed on a module over the raw REPL, compiled in RAM, with nothing
   written to flash;
2. counted on the PC with the simulator's rule (Python calls plus C calls
   made from firmware code).

Device time divided by counted calls gives `per_call_us` for
scripts/mesh_sim.py. The script also measures garbage-collection pauses
and allocation per call, which set the simulator's GC model.

    py -3 scripts/diagnostics/mesh_cpu_benchmark.py --port COM16
    py -3 scripts/diagnostics/mesh_cpu_benchmark.py --pc-only

The firmware on the module is interrupted while this runs. When the run
finishes, the module is soft-reset, which restarts its firmware.
"""

from __future__ import print_function

import argparse
import os

import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MAIN = os.path.join(ROOT, "main")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, MAIN)

FW_NAME = "<benchfw>"

# Imports removed from module_link.py; the bench defines these names itself.
STRIP_IMPORTS = ("import module_ota\n", "import nrf_module_common\n", "from nfcp import *\n",
                 "from mesh import *\n")


def strip_nfcp_builders(text):
    """Drop NFCP's build_* methods (the receive path never calls them) so the
    source fits the module's RAM when compiled over the raw REPL."""
    out = []
    skipping = False
    for line in text.splitlines(True):
        if line.startswith("    def "):
            name = line[8:].split("(", 1)[0]
            skipping = name.startswith("build_") or name == "_encode_fire_mask"
        elif skipping and line.strip() and not line.startswith("    "):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def firmware_source(device=False):
    # One namespace, like the field bundle. bridge_radio goes first so the
    # names it shares with nfcp (CMD_PING, crc16_ccitt) end up as nfcp's.
    parts = []
    for name in ("bridge_radio.py", "nfcp.py", "mesh.py", "module_link.py"):
        with open(os.path.join(MAIN, name), encoding="utf-8") as handle:
            text = handle.read().replace("\r\n", "\n")
        if name == "module_link.py":
            for line in STRIP_IMPORTS:
                assert line in text, line
                text = text.replace(line, "")
        if device and name == "nfcp.py":
            text = strip_nfcp_builders(text)
        parts.append(text)
    parts.append(FW_EXTRA)
    return "\n".join(parts)


# Firmware-side helpers: counted, like firmware code in the simulator.
FW_EXTRA = '''
class module_ota:
    pass

class nrf_module_common:
    pass

class BenchNode:
    firmware_version = "bench"
    def ids(self):
        return 0x04D2, 0x0001, 0xD96E30DD, 90
    def armed(self):
        return False

def bench_handler(pkt):
    return pkt.get("cmd")

def bench_nop():
    pass
'''

# Harness: identical on both sides; not counted.
HARNESS = '''
import gc

def bench_payloads():
    m = NFCP(node_id=0x01)
    info = bytes(range(97))
    rsp = m.build_response(0x11223344, CMD_INFO_RSP, info)
    req_other = m.build_info_request(0x11223344)
    req_me = m.build_info_request(0xD96E30DD)
    hb = m.build_script_heartbeat_broadcast(0x4D53, 1200)
    fire = m.build_group_fire(0x0001, 0x2)
    net = (0x04D2).to_bytes(2, "big")
    out = []
    for label, frame in (("rsp_other_116", rsp), ("req_other_14", req_other),
                         ("req_me_new", req_me), ("hb_bcast_new", hb),
                         ("fire_group_new", fire), ("req_me_dup", req_me)):
        # Mesh v1 payloads, as a bridge (or another module) sends them.
        out.append((label, mesh_build(0x04D2, 0x0B0B0B0B, 7, frame)))
    usb = build_send_nfcp(5, req_me)
    return out, usb

def bench_run(clock, reps, link_cls):
    payloads, usb = bench_payloads()
    results = []
    node = BenchNode()

    t0 = clock()
    for _ in range(reps):
        pass
    loop_base = clock() - t0

    t0 = clock()
    for _ in range(reps):
        bench_nop()
    results.append(("nop_call", (clock() - t0 - loop_base) / reps))

    for label, payload in payloads:
        link = link_cls(node, 0x67676767, 8)
        if label.endswith("_dup"):
            link.receive(payload, bench_handler)
        t0 = clock()
        for _ in range(reps):
            if not label.endswith("_dup"):
                link.seen.keys = {}
            link.queue.items = []
            link.receive(payload, bench_handler)
        results.append((label, (clock() - t0 - loop_base) / reps))

    parser = BridgeFrameParser()
    t0 = clock()
    for _ in range(reps):
        for b in usb:
            parser.feed(b)
    results.append(("bridge_usb_frame", (clock() - t0 - loop_base) / reps))
    return results
'''

DEVICE_MAIN = '''
import time
def _clock():
    return time.ticks_us()
def _run():
    gc.collect()
    res = bench_run(_clock, %(reps)d, ModuleLink)
    for label, us in res:
        print("BENCH", label, "%%.1f" %% us)
    # Allocation per receive of the long frame (GC off while measuring).
    payloads, usb = bench_payloads()
    link = ModuleLink(BenchNode(), 0x67676767, 8)
    gc.collect()
    gc.disable()
    a0 = gc.mem_alloc()
    for _ in range(20):
        link.receive(payloads[0][1], bench_handler)
    a1 = gc.mem_alloc()
    gc.enable()
    print("ALLOC rsp_other_116 %%.1f" %% ((a1 - a0) / 20))
    junk = [bytearray(64) for _ in range(300)]
    t0 = time.ticks_us()
    gc.collect()
    print("GC busy_heap_us", time.ticks_diff(time.ticks_us(), t0))
    junk = None
    t0 = time.ticks_us()
    gc.collect()
    print("GC idle_heap_us", time.ticks_diff(time.ticks_us(), t0))
    print("HEAP free", gc.mem_free(), "alloc", gc.mem_alloc())
_run()
'''


def run_device(port_name, reps):
    import serial
    import raw_micropython_upload as raw

    # Payloads are built on the PC and sent as literals, so the module needs
    # no frame builders.
    payloads, usb = pc_namespace()["bench_payloads"]()
    literal = "\ndef bench_payloads():\n    return %r, %r\n" % (payloads, bytes(usb))
    code = (firmware_source(device=True) + "\n" + HARNESS + literal + "\n" +
            DEVICE_MAIN % {"reps": reps})
    with serial.Serial(port_name, 115200, timeout=0.05, write_timeout=5) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            out = raw.exec_raw(port, code, timeout=120).decode("utf-8", "replace")
        finally:
            raw.exit_raw_repl(port)
            port.write(raw.CTRL_D)  # soft reset: main.py restarts the firmware
    return out


def pc_namespace():
    """The benchmark firmware and harness, loaded on the PC."""
    fake_time = types.ModuleType("time")
    fake_time.ticks_ms = lambda: 1000
    fake_time.ticks_us = lambda: 1000000
    fake_time.ticks_diff = lambda a, b: a - b
    fake_time.ticks_add = lambda a, b: a + b
    fake_time.sleep_ms = lambda ms: None
    saved = {name: sys.modules.get(name) for name in ("time", "machine", "nrf_radio")}
    sys.modules["time"] = fake_time
    sys.modules["machine"] = types.ModuleType("machine")
    sys.modules["nrf_radio"] = types.ModuleType("nrf_radio")
    try:
        ns = {"__name__": "bench"}
        exec(compile(firmware_source(), FW_NAME, "exec"), ns)
        exec(compile(HARNESS, "<harness>", "exec"), ns)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    return ns


def count_pc(reps=20):
    """(calls, lines) per operation, counted with the simulator's rule."""
    ns = pc_namespace()
    counter = {"calls": 0, "lines": 0}

    def profile(frame, event, arg):
        if (event == "call" or event == "c_call") and frame.f_code.co_filename == FW_NAME:
            counter["calls"] += 1

    def lines(frame, event, arg):
        if event == "line":
            counter["lines"] += 1
        return lines

    def trace(frame, event, arg):
        if event == "call" and frame.f_code.co_filename == FW_NAME:
            return lines
        return None

    def run(key):
        def clock():
            return counter[key]
        sys.setprofile(profile)
        sys.settrace(trace)
        try:
            return dict(ns["bench_run"](clock, reps, ns["ModuleLink"]))
        finally:
            sys.settrace(None)
            sys.setprofile(None)

    calls = run("calls")
    line_counts = run("lines")
    return dict((label, (calls[label], line_counts[label])) for label in calls)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="COM16")
    parser.add_argument("--reps", type=int, default=40)
    parser.add_argument("--pc-only", action="store_true")
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from mesh_sim import CpuModel
    model = CpuModel()
    counts = count_pc()
    if args.pc_only:
        for label, (n_calls, n_lines) in counts.items():
            print("%-18s %7.1f calls %8.1f lines  model %8.1f us" % (
                label, n_calls, n_lines, n_calls * model.per_call_us + n_lines * model.per_line_us))
        return 0

    out = run_device(args.port, args.reps)
    device = {}
    extra = []
    for line in out.splitlines():
        parts = line.split()
        if parts[:1] == ["BENCH"]:
            device[parts[1]] = float(parts[2])
        elif parts:
            extra.append(line.strip())

    # Compare with the simulator's model (scripts/mesh_sim.py CpuModel), and
    # refit both terms by least squares on the receive workloads.
    print("%-18s %10s %7s %7s %10s %7s" % ("workload", "device_us", "calls", "lines", "model_us", "error"))
    rows = []
    for label, (n_calls, n_lines) in counts.items():
        us = device.get(label)
        if us is None:
            continue
        predicted = n_calls * model.per_call_us + n_lines * model.per_line_us
        print("%-18s %10.1f %7.1f %7.1f %10.1f %+6.0f%%" % (
            label, us, n_calls, n_lines, predicted, 100.0 * (predicted - us) / us if us else 0))
        if label != "nop_call":
            rows.append((n_calls, n_lines, us))
    s11 = sum(c * c for c, l, y in rows)
    s12 = sum(c * l for c, l, y in rows)
    s22 = sum(l * l for c, l, y in rows)
    det = s11 * s22 - s12 * s12
    if rows and det:
        t1 = sum(c * y for c, l, y in rows)
        t2 = sum(l * y for c, l, y in rows)
        print("refit: %.1f us per call + %.2f us per line (model: %.1f + %.2f)" % (
            (t1 * s22 - t2 * s12) / det, (s11 * t2 - s12 * t1) / det,
            model.per_call_us, model.per_line_us))
    for line in extra:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
