"""Hardware smoke test for mesh firmware: drive the USB bridge from the PC
the way the app does, and check a field module answers over the mesh.

No ARM and no FIRE are sent. It runs:
- discover, info, output status, a resistance scan;
- script clear + load, then disarm;
- N repeated info requests, for reliability and round-trip time.
The module's console (optional --module-port) is captured, so the trace
shows each command was handled once even though the bridge sends 3 copies.

    py -3 scripts/diagnostics/mesh_hw_smoke.py --bridge COM12 --module-port COM16
"""

from __future__ import print_function

import argparse
import os
import statistics
import sys
import threading
import time

import serial

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "main"))
import bridge_radio  # noqa: E402
import nfcp  # noqa: E402

PREFIX = b"NFUSB1 "


class Bridge(object):
    def __init__(self, port_name):
        self.port = serial.Serial(port_name, 115200, timeout=0.05, write_timeout=5)
        self.write_lock = threading.Lock()   # the heartbeat thread writes too
        self.parser = bridge_radio.BridgeFrameParser()
        self.lock = threading.Lock()
        self.responses = {}
        self.rx = []          # (time, rssi, pkt)
        self.console = []
        self.seq = 0
        self.running = True
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        buf = b""
        while self.running:
            chunk = self.port.read(512)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line.startswith(PREFIX):
                    if line:
                        self.console.append(line.decode("utf-8", "replace"))
                    continue
                try:
                    data = bytes.fromhex(line[len(PREFIX):].decode("ascii"))
                except ValueError:
                    continue
                for value in data:
                    frame = self.parser.feed(value)
                    if frame is None:
                        continue
                    now = time.monotonic()
                    if frame.cmd == bridge_radio.CMD_NFCP_RX:
                        info = bridge_radio.parse_nfcp_rx(frame.payload)
                        p = nfcp.NFCPStreamParser()
                        pkt = None
                        for b in info["nfcp_frame"]:
                            pkt = p.feed(b) or pkt
                        if pkt:
                            with self.lock:
                                self.rx.append((now, info["rssi"], pkt))
                    elif frame.flags & bridge_radio.FLAG_RESPONSE:
                        with self.lock:
                            self.responses[frame.seq] = frame

    def request(self, cmd, payload=b"", timeout=3.0):
        with self.write_lock:
            self.seq = (self.seq % 250) + 1
            seq = self.seq
            frame = bridge_radio.build_frame(seq, cmd, payload)
            self.port.write(PREFIX + frame.hex().encode("ascii") + b"\n")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                reply = self.responses.pop(seq, None)
            if reply is not None:
                return reply
            time.sleep(0.005)
        raise TimeoutError("no bridge response to command 0x%02X" % cmd)

    def send_nfcp(self, nfcp_frame):
        # Timestamp before writing: the module's reply can reach USB before
        # this thread sees the bridge's ack (Windows sleeps are ~15 ms).
        started = time.monotonic()
        reply = self.request(bridge_radio.CMD_SEND_NFCP, nfcp_frame)
        if reply.flags & bridge_radio.FLAG_ERROR:
            raise RuntimeError("bridge rejected frame: %s" % bytes(reply.payload).hex())
        return started

    def wait_rx(self, predicate, since, timeout=2.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for t, rssi, pkt in self.rx:
                    if t >= since and predicate(pkt):
                        return t, rssi, pkt
            time.sleep(0.005)
        return None

    def count_rx(self, predicate, since):
        with self.lock:
            return sum(1 for t, _, pkt in self.rx if t >= since and predicate(pkt))

    def close(self):
        self.running = False
        self.reader.join(1)
        self.port.close()


class Console(object):
    def __init__(self, port_name):
        self.lines = []
        self.port = serial.Serial(port_name, 115200, timeout=0.1)
        self.running = True
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        while self.running:
            line = self.port.readline()
            if line:
                self.lines.append((time.monotonic(), line.decode("utf-8", "replace").rstrip()))

    def close(self):
        self.running = False
        self.thread.join(1)
        self.port.close()


def response_from(uid, cmd, acked=None):
    def match(pkt):
        if pkt.get("frame_type") != nfcp.SOF_RESPONSE or pkt.get("src") != uid or pkt.get("cmd") != cmd:
            return False
        if acked is not None:
            data = bytes(pkt.get("data") or b"")
            return len(data) == 2 and data[0] == acked
        return True
    return match


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bridge", default="COM12")
    ap.add_argument("--module-port", help="field module console port (optional)")
    ap.add_argument("--repeat", type=int, default=20)
    args = ap.parse_args()

    console = Console(args.module_port) if args.module_port else None
    bridge = Bridge(args.bridge)
    master = nfcp.NFCP(node_id=0x01)
    results = []
    session = os.urandom(4)
    stop_hb = threading.Event()

    def heartbeat():
        while not stop_hb.is_set():
            try:
                bridge.request(bridge_radio.CMD_HOST_HEARTBEAT, session, timeout=1.0)
            except Exception as error:
                print("heartbeat:", error)
            stop_hb.wait(0.8)

    def check(label, ok, detail=""):
        results.append((label, ok))
        print("%-4s %-28s %s" % ("PASS" if ok else "FAIL", label, detail))

    try:
        info = bridge.request(bridge_radio.CMD_GET_INFO)
        version = bytes(info.payload[9:9 + info.payload[8]]).decode()
        check("bridge info", True, "%s node=%08X" % (version, int.from_bytes(info.payload[:4], "big")))
        threading.Thread(target=heartbeat, daemon=True).start()
        time.sleep(0.5)

        since = bridge.send_nfcp(master.build_discover_broadcast(nonce=0x4D53))
        time.sleep(1.5)
        with bridge.lock:
            found = {}
            for t, rssi, pkt in bridge.rx:
                if t >= since and pkt.get("cmd") == nfcp.CMD_DISCOVER_RSP:
                    found.setdefault(pkt["src"], (rssi, t - since))
        check("discover", bool(found), " ".join("%08X rssi=%d %.0fms" % (u, r, d * 1000) for u, (r, d) in found.items()))
        if not found:
            return 1
        uid = sorted(found)[0]
        copies = bridge.count_rx(lambda p: p.get("cmd") == nfcp.CMD_DISCOVER_RSP and p.get("src") == uid, since)
        check("discover reply deduplicated", copies == 1, "%d copies forwarded to USB" % copies)

        since = bridge.send_nfcp(master.build_info_request(uid))
        hit = bridge.wait_rx(response_from(uid, nfcp.CMD_INFO_RSP), since)
        detail = ""
        if hit:
            data = bytes(hit[2]["data"])
            fields = []
            i = 0
            while i < len(data):
                n = data[i]
                fields.append(data[i + 1:i + 1 + n].decode("utf-8", "replace"))
                i += 1 + n
            detail = "%s %s battery=%smV, %.0f ms" % (fields[0], fields[1], fields[6] if len(fields) > 6 else "?", (hit[0] - since) * 1000)
        check("info", hit is not None, detail)

        since = bridge.send_nfcp(master.build_output_status_request(uid))
        hit = bridge.wait_rx(response_from(uid, nfcp.CMD_OUTPUT_STATUS_RSP), since)
        check("output status", hit is not None, ("%.0f ms" % ((hit[0] - since) * 1000)) if hit else "")

        since = bridge.send_nfcp(master.build_resistance_request(uid))
        hit = bridge.wait_rx(response_from(uid, nfcp.CMD_RESISTANCE_RSP), since, timeout=5.0)
        check("resistance scan", hit is not None, ("%.0f ms, %d bytes" % ((hit[0] - since) * 1000, len(hit[2]["data"]))) if hit else "")

        sid = 0x4D53
        since = bridge.send_nfcp(master.build_script_clear(uid, sid))
        ok_clear = bridge.wait_rx(response_from(uid, nfcp.CMD_ACK, nfcp.CMD_SCRIPT_CLEAR), since)
        since = bridge.send_nfcp(master.build_script_load(uid, sid, 0, [(1000, 0x1), (2000, 0x2), (3000, 0x4)]))
        ok_load = bridge.wait_rx(response_from(uid, nfcp.CMD_ACK, nfcp.CMD_SCRIPT_LOAD), since)
        check("script clear + load", bool(ok_clear and ok_load), "clear=%s load=%s" % (bool(ok_clear), bool(ok_load)))

        since = bridge.send_nfcp(master.build_disarm_unique(uid))
        hit = bridge.wait_rx(response_from(uid, nfcp.CMD_ACK, nfcp.CMD_DISARM), since)
        check("disarm", hit is not None, ("%.0f ms" % ((hit[0] - since) * 1000)) if hit else "")

        rtts = []
        lost = 0
        for i in range(args.repeat):
            since = bridge.send_nfcp(master.build_ping(uid, bytes([i & 0xFF, 0x5A])))
            hit = bridge.wait_rx(lambda p, i=i: (p.get("frame_type") == nfcp.SOF_RESPONSE and p.get("src") == uid and
                                                 p.get("cmd") == nfcp.CMD_PING and bytes(p["data"][:1]) == bytes([i & 0xFF])),
                                 since, timeout=1.5)
            if hit:
                rtts.append((hit[0] - since) * 1000)
            else:
                lost += 1
            time.sleep(0.05)
        if rtts:
            check("ping x%d" % args.repeat, lost == 0, "lost %d, round trip median %.0f ms, min %.0f, max %.0f" % (
                lost, statistics.median(rtts), min(rtts), max(rtts)))
        else:
            check("ping x%d" % args.repeat, False, "no replies")

        # Phase 3: reliable delivery (CMD_SEND_NFCP_RELIABLE) on mesh bridges.
        version_ok = version.startswith("usb-master-0.4.") and int(version.rsplit(".", 1)[1]) >= 2
        if version_ok or version.startswith("usb-monitor-0.3.3"):
            routes = {0: "direct", 1: "via", 2: "flood"}

            def reliable(frame, match, timeout_ms=1500):
                started = time.monotonic()
                payload = uid.to_bytes(4, "big") + timeout_ms.to_bytes(2, "big") + frame
                answer = bridge.request(bridge_radio.CMD_SEND_NFCP_RELIABLE, payload, timeout=timeout_ms / 1000.0 + 2)
                hit = bridge.wait_rx(match, started, timeout=0.5)
                return answer.payload, hit, (time.monotonic() - started) * 1000

            result, hit, ms = reliable(master.build_info_request(uid), response_from(uid, nfcp.CMD_INFO_RSP))
            check("reliable info", result[0] == 0 and hit is not None,
                  "route=%s attempts=%d, %.0f ms" % (routes.get(result[1], result[1]), result[6], ms))
            times = []
            failures = 0
            for i in range(50):
                data = bytes([i & 0xFF, 0xA5])
                result, hit, ms = reliable(master.build_ping(uid, data), lambda p, d=data: (
                    p.get("cmd") == nfcp.CMD_PING and p.get("src") == uid and bytes(p["data"][:2]) == d))
                if result[0] == 0 and hit:
                    times.append(ms)
                else:
                    failures += 1
            check("reliable ping x50", failures == 0, "failed %d, round trip median %.0f ms, max %.0f" % (
                failures, statistics.median(times) if times else 0, max(times) if times else 0))
        stats = bridge.request(bridge_radio.CMD_GET_STATS).payload
        names = ("tx_ok", "tx_timeout", "rx_ok", "rx_timeout", "rx_crc_error", "rx_bad_len")
        values = [int.from_bytes(stats[i * 4:i * 4 + 4], "big") for i in range(6)]
        print("bridge radio stats:", ", ".join("%s=%d" % kv for kv in zip(names, values)),
              "address=0x%08X" % int.from_bytes(stats[30:34], "big"))
    finally:
        stop_hb.set()
        time.sleep(0.2)
        bridge.close()
        if console:
            time.sleep(0.3)
            console.close()
            print("--- module console")
            for t, line in console.lines:
                print("   ", line)
    failed = [label for label, ok in results if not ok]
    print("%d checks, %d failed%s" % (len(results), len(failed), (": " + ", ".join(failed)) if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
