# PC_multi_module_emulator.py
# NightFire / NFCP multi-module emulator for a PC serial port.
#
# Put this file in the same folder as nfcp.py, then run:
#   python PC_multi_module_emulator.py --port COM18
#
# The script emulates multiple dummy modules on one serial port. Each virtual
# module has its own UNIQUE_ID and GROUP_ID and responds like main.py:
# DISCOVER, ARM, DISARM, PING, CONTINUITY_REQ, SET_GROUP, SET_UNIQUE, and FIRE log.

import argparse
import json
import os
import random
import signal
import time
from dataclasses import dataclass, field
from typing import List, Optional

import serial

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "main"))
from nfcp import *  # noqa: E402


# -------------------------
# Defaults
# -------------------------
DEFAULT_SERIAL_PORT = "COM18"
DEFAULT_BAUDRATE = 256000
DEFAULT_TIMEOUT = 0.01

DEFAULT_MODULE_COUNT = 8
DEFAULT_BASE_UNIQUE = 0x12345000
DEFAULT_GROUP_START = 0x0001
DEFAULT_CUE_COUNT = 16
DEFAULT_DEVICE_TYPE = 0x10
DEFAULT_PROTO_VER = 0x10
DEFAULT_FLAGS = 0x00

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pc_virtual_modules.json")

# Match the dummy module timing/behavior from main.py closely enough for PC testing.
DISCOVER_BACKOFF_MIN_MS = 10
DISCOVER_BACKOFF_MAX_MS = 250
CONT_MIN_INTERVAL_MS = 100
DEFAULT_OPEN_CUE_COUNT = 2
SIM_REFRESH_MS = 2500


# -------------------------
# Small helpers
# -------------------------
def now_ms() -> int:
    return int(time.monotonic() * 1000)


def hx(s: str) -> int:
    s = s.strip().lower().replace("_", "")
    if s.startswith("0x"):
        s = s[2:]
    return int(s, 16)


def fmt_uid(uid: int) -> str:
    return f"0x{uid & 0xFFFFFFFF:08X}"


def fmt_gid(gid: int) -> str:
    return f"0x{gid & 0xFFFF:04X}"


def mask_to_cues(mask: int, cue_count: int) -> List[int]:
    return [i for i in range(1, cue_count + 1) if mask & (1 << (i - 1))]


def clamp_cue_count(cue_count: int) -> int:
    return max(1, min(int(cue_count), 64))


@dataclass
class VirtualModule:
    unique_id: int
    group_id: int
    cue_count: int = DEFAULT_CUE_COUNT
    device_type: int = DEFAULT_DEVICE_TYPE
    proto_ver: int = DEFAULT_PROTO_VER
    flags: int = DEFAULT_FLAGS
    armed: bool = False
    last_cont_ms: int = 0
    open_cue_count: int = DEFAULT_OPEN_CUE_COUNT
    sim_open: set = field(default_factory=set)
    sim_last_refresh_ms: int = 0

    def to_json(self) -> dict:
        return {
            "unique_id": fmt_uid(self.unique_id),
            "group_id": fmt_gid(self.group_id),
            "cue_count": int(self.cue_count),
            "device_type": f"0x{self.device_type & 0xFF:02X}",
            "proto_ver": f"0x{self.proto_ver & 0xFF:02X}",
            "flags": f"0x{self.flags & 0xFF:02X}",
            "armed": bool(self.armed),
            "open_cue_count": int(self.open_cue_count),
        }

    @staticmethod
    def from_json(d: dict) -> "VirtualModule":
        return VirtualModule(
            unique_id=hx(str(d.get("unique_id", "0x12345678"))) & 0xFFFFFFFF,
            group_id=hx(str(d.get("group_id", "0x0001"))) & 0xFFFF,
            cue_count=clamp_cue_count(d.get("cue_count", DEFAULT_CUE_COUNT)),
            device_type=hx(str(d.get("device_type", "0x10"))) & 0xFF,
            proto_ver=hx(str(d.get("proto_ver", "0x10"))) & 0xFF,
            flags=hx(str(d.get("flags", "0x00"))) & 0xFF,
            armed=bool(d.get("armed", False)),
            open_cue_count=int(d.get("open_cue_count", DEFAULT_OPEN_CUE_COUNT)),
        )

    def address_matches(self, pkt: dict) -> bool:
        ft = pkt.get("frame_type")
        if ft == SOF_BROADCAST:
            return True
        if ft == SOF_GROUP:
            return pkt.get("target") == self.group_id
        if ft == SOF_UNIQUE:
            return pkt.get("target") == self.unique_id
        return False

    def refresh_open_cues(self):
        t = now_ms()
        if self.sim_last_refresh_ms == 0 or (t - self.sim_last_refresh_ms) >= SIM_REFRESH_MS:
            self.sim_last_refresh_ms = t
            n = min(max(0, int(self.open_cue_count)), max(0, self.cue_count))
            self.sim_open = set(random.sample(range(1, self.cue_count + 1), n)) if n else set()

    def build_open_mask(self) -> int:
        """
        Build the current NFCP continuity open mask.

        Bit mapping: bit0 = cue 1, bit63 = cue 64.
        Mask meaning: 1 = open / no continuity, 0 = OK / closed.
        """
        self.refresh_open_cues()
        open_mask = 0
        for cue in self.sim_open:
            if 1 <= cue <= self.cue_count:
                open_mask |= (1 << (cue - 1))
        return open_mask & 0xFFFFFFFFFFFFFFFF

    def build_continuity_payload(self) -> bytes:
        cue_count = clamp_cue_count(self.cue_count)
        nbytes = (cue_count + 7) // 8
        return self.build_open_mask().to_bytes(nbytes, "big")


class MultiModuleEmulator:
    def __init__(self, ser: serial.Serial, modules: List[VirtualModule], verbose: bool = True, cfg_path: str = CFG_PATH):
        self.ser = ser
        self.modules = modules
        self.verbose = verbose
        self.cfg_path = cfg_path
        self.parser = NFCPStreamParser()
        self.nfcp = NFCP(node_id=0x01)
        self.running = True

    def log(self, msg: str):
        if self.verbose:
            print(msg, flush=True)

    def tx_bytes(self, data: bytes, label: str = ""):
        if label:
            self.log(f"TX {label}: {data.hex(' ').upper()}")
        self.ser.write(data)
        self.ser.flush()

    def save_cfg(self):
        save_modules(self.modules, self.cfg_path)

    def handle_packet(self, pkt: dict):
        cmd = pkt.get("cmd")
        matched = [m for m in self.modules if m.address_matches(pkt)]
        if not matched:
            return

        if cmd == CMD_DISCOVER:
            nonce = None
            data = pkt.get("data", b"") or b""
            if len(data) >= 2:
                nonce = int.from_bytes(data[:2], "big")

            # Simulate collision-safe random backoff by responding one module at a time.
            random.shuffle(matched)
            self.log(f"RX DISCOVER -> {len(matched)} response(s)")
            for m in matched:
                delay = random.randint(DISCOVER_BACKOFF_MIN_MS, DISCOVER_BACKOFF_MAX_MS) / 1000.0
                time.sleep(delay)
                rsp = self.nfcp.build_discover_response(
                    src_unique=m.unique_id,
                    group_id=m.group_id,
                    unique_id=m.unique_id,
                    cue_count=m.cue_count,
                    device_type=m.device_type,
                    proto_ver=m.proto_ver,
                    flags=m.flags,
                    nonce=nonce,
                )
                self.tx_bytes(rsp, f"DISCOVER_RSP uid={fmt_uid(m.unique_id)} group={fmt_gid(m.group_id)}")
            return

        for m in matched:
            self.handle_for_module(m, pkt)

    def handle_for_module(self, m: VirtualModule, pkt: dict):
        cmd = pkt.get("cmd")
        data = pkt.get("data", b"") or b""

        if cmd == CMD_PING:
            # Same as main.py: PING responds only when addressed by UNIQUE_ID.
            if pkt.get("frame_type") != SOF_UNIQUE or pkt.get("target") != m.unique_id:
                return
            rsp_data = data if data else bytes([PING_REPLY_BYTE])
            self.tx_bytes(self.nfcp.build_response(m.unique_id, CMD_PING, rsp_data), f"PING uid={fmt_uid(m.unique_id)}")
            return

        if cmd == CMD_ARM:
            m.armed = True
            self.log(f"ARMED uid={fmt_uid(m.unique_id)} group={fmt_gid(m.group_id)}")
            return

        if cmd == CMD_DISARM:
            m.armed = False
            self.log(f"DISARMED uid={fmt_uid(m.unique_id)} group={fmt_gid(m.group_id)}")
            return

        if cmd == CMD_FIRE:
            mask = int.from_bytes(data or b"\x00", "big")
            cues = mask_to_cues(mask, min(m.cue_count, 64))
            state = "ARMED" if m.armed else "DISARMED"
            self.log(f"FIRE rx uid={fmt_uid(m.unique_id)} group={fmt_gid(m.group_id)} state={state} mask=0x{mask:X} cues={cues} (ignored by emulator)")
            return

        if cmd == CMD_CONTINUITY_REQ:
            if pkt.get("frame_type") != SOF_UNIQUE or pkt.get("target") != m.unique_id:
                return
            t = now_ms()
            if (t - m.last_cont_ms) < CONT_MIN_INTERVAL_MS:
                return
            m.last_cont_ms = t
            open_mask = m.build_open_mask()
            cont_payload = m.build_continuity_payload()
            open_cues = sorted(m.sim_open)
            rsp = self.nfcp.build_response(m.unique_id, CMD_CONTINUITY_RSP, cont_payload)
            self.tx_bytes(
                rsp,
                f"CONT_RSP uid={fmt_uid(m.unique_id)} open_cues={open_cues} open_mask=0x{open_mask:X}"
            )
            return

        if cmd == CMD_SET_GROUP:
            if pkt.get("frame_type") != SOF_UNIQUE or pkt.get("target") != m.unique_id:
                return
            if m.armed:
                self.tx_bytes(self.nfcp.build_nack(m.unique_id, CMD_SET_GROUP, NACK_REJECTED_ARMED), f"NACK SET_GROUP uid={fmt_uid(m.unique_id)} armed")
                return
            if len(data) != 2:
                self.tx_bytes(self.nfcp.build_nack(m.unique_id, CMD_SET_GROUP, NACK_BAD_LENGTH), f"NACK SET_GROUP uid={fmt_uid(m.unique_id)} bad_len")
                return
            m.group_id = int.from_bytes(data, "big") & 0xFFFF
            self.tx_bytes(self.nfcp.build_ack(m.unique_id, CMD_SET_GROUP, ACK_OK), f"ACK SET_GROUP uid={fmt_uid(m.unique_id)} new_group={fmt_gid(m.group_id)}")
            self.save_cfg()
            return

        if cmd == CMD_SET_UNIQUE:
            if pkt.get("frame_type") != SOF_UNIQUE or pkt.get("target") != m.unique_id:
                return
            old_uid = m.unique_id
            if m.armed:
                self.tx_bytes(self.nfcp.build_nack(old_uid, CMD_SET_UNIQUE, NACK_REJECTED_ARMED), f"NACK SET_UNIQUE uid={fmt_uid(old_uid)} armed")
                return
            if len(data) != 4:
                self.tx_bytes(self.nfcp.build_nack(old_uid, CMD_SET_UNIQUE, NACK_BAD_LENGTH), f"NACK SET_UNIQUE uid={fmt_uid(old_uid)} bad_len")
                return
            new_uid = int.from_bytes(data, "big") & 0xFFFFFFFF
            if any(x is not m and x.unique_id == new_uid for x in self.modules):
                self.tx_bytes(self.nfcp.build_nack(old_uid, CMD_SET_UNIQUE, NACK_INVALID_VALUE), f"NACK SET_UNIQUE duplicate={fmt_uid(new_uid)}")
                return
            self.tx_bytes(self.nfcp.build_ack(old_uid, CMD_SET_UNIQUE, ACK_OK), f"ACK SET_UNIQUE old={fmt_uid(old_uid)} new={fmt_uid(new_uid)}")
            m.unique_id = new_uid
            self.save_cfg()
            return

        self.log(f"Unhandled cmd=0x{cmd:02X} for uid={fmt_uid(m.unique_id)}")

    def run(self):
        self.log("\nVirtual modules online:")
        for i, m in enumerate(self.modules, 1):
            self.log(f"  {i:02d}: uid={fmt_uid(m.unique_id)} group={fmt_gid(m.group_id)} cues={m.cue_count} open_cues={m.open_cue_count} type=0x{m.device_type:02X}")
        self.log("\nListening for NFCP frames. Press Ctrl+C to stop.\n")

        while self.running:
            chunk = self.ser.read(256)
            if chunk:
                for b in chunk:
                    pkt = self.parser.feed(b)
                    if pkt:
                        self.handle_packet(pkt)
            else:
                time.sleep(0.001)


# -------------------------
# Config generation/loading
# -------------------------
def generate_modules(count: int, base_unique: int, group_start: int, cue_count: int, unique_groups: bool, open_cue_count: int) -> List[VirtualModule]:
    modules = []
    cue_count = clamp_cue_count(cue_count)
    for i in range(count):
        uid = (base_unique + i) & 0xFFFFFFFF
        gid = (group_start + i) & 0xFFFF if unique_groups else group_start & 0xFFFF
        modules.append(VirtualModule(unique_id=uid, group_id=gid, cue_count=cue_count, open_cue_count=open_cue_count))
    return modules


def save_modules(modules: List[VirtualModule], path: str):
    data = {
        "generated_epoch": time.time(),
        "module_count": len(modules),
        "modules": [m.to_json() for m in modules],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_modules(path: str) -> Optional[List[VirtualModule]]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mods = [VirtualModule.from_json(d) for d in data.get("modules", [])]
    return mods or None


def ask_int(prompt: str, default: int, base: int = 10) -> int:
    s = input(f"{prompt} [{default if base == 10 else hex(default)}]: ").strip()
    if not s:
        return default
    if base == 16 or s.lower().startswith("0x"):
        return hx(s)
    return int(s, 10)


def get_modules_from_user(args) -> List[VirtualModule]:
    if args.load and not args.reset:
        loaded = load_modules(args.config)
        if loaded:
            # With --load, keep each module's saved open_cue_count unless
            # --open-cues is explicitly supplied on the command line.
            if args.open_cues is not None:
                for m in loaded:
                    m.open_cue_count = max(0, min(int(args.open_cues), int(m.cue_count)))
            return loaded

    count = args.count
    if count is None:
        count = ask_int("How many modules do you want to emulate?", DEFAULT_MODULE_COUNT, 10)

    base_unique = args.base_unique if args.base_unique is not None else ask_int("Starting UNIQUE_ID hex", DEFAULT_BASE_UNIQUE, 16)
    group_start = args.group_start if args.group_start is not None else ask_int("Starting GROUP_ID hex", DEFAULT_GROUP_START, 16)

    cue_count = clamp_cue_count(args.cues)
    open_cue_count = args.open_cues
    if open_cue_count is None:
        open_cue_count = ask_int("How many cues should be open per module?", DEFAULT_OPEN_CUE_COUNT, 10)
    open_cue_count = max(0, min(int(open_cue_count), int(cue_count)))
    unique_groups = not args.same_group
    modules = generate_modules(count, base_unique, group_start, cue_count, unique_groups, open_cue_count)
    save_modules(modules, args.config)
    return modules


# -------------------------
# Main
# -------------------------
def main():
    parser = argparse.ArgumentParser(description="NFCP PC multi-module emulator")
    parser.add_argument("--port", default=DEFAULT_SERIAL_PORT, help="Serial port, e.g. COM18 or /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUDRATE, help="Serial baudrate")
    parser.add_argument("--count", type=int, default=None, help="Number of virtual modules to generate")
    parser.add_argument("--base-unique", type=lambda s: hx(s), default=None, help="First unique ID, hex, e.g. 0x12345000")
    parser.add_argument("--group-start", type=lambda s: hx(s), default=None, help="First group ID, hex, e.g. 0x0001")
    parser.add_argument("--same-group", action="store_true", help="Put every virtual module in the same group instead of unique groups")
    parser.add_argument("--cues", type=int, default=DEFAULT_CUE_COUNT, help="Cue count per module")
    parser.add_argument("--open-cues", type=int, default=None, help="Number of simulated open cues per module; if omitted, you will be prompted")
    parser.add_argument("--config", default=CFG_PATH, help="Virtual module config JSON path")
    parser.add_argument("--load", action="store_true", help="Load existing config JSON instead of generating new modules")
    parser.add_argument("--reset", action="store_true", help="Ignore old config and regenerate modules")
    parser.add_argument("--quiet", action="store_true", help="Less console logging")
    args = parser.parse_args()

    modules = get_modules_from_user(args)

    try:
        ser = serial.Serial(args.port, args.baud, timeout=DEFAULT_TIMEOUT)
    except Exception as e:
        print(f"Serial error opening {args.port}: {e}")
        return 1

    emulator = MultiModuleEmulator(ser, modules, verbose=not args.quiet, cfg_path=args.config)

    def stop(_sig=None, _frame=None):
        emulator.running = False

    signal.signal(signal.SIGINT, stop)
    try:
        emulator.run()
    finally:
        save_modules(modules, args.config)
        ser.close()
        print("\nEmulator stopped. Config saved to", args.config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
