from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import direct_exec_micropython_code as direct


CODE = r'''
import __main__ as master

SCRIPT_ID = {script_id}
ENTRY_COUNT = {entry_count}
INTERVAL_MS = {interval_ms}
START_DELAY_MS = {start_delay_ms}
HEARTBEAT_MS = {heartbeat_ms}


def make_entries(uid_index):
    entries = []
    for i in range(ENTRY_COUNT):
        cue = i % 4
        mask = 1 << cue
        entries.append((i * INTERVAL_MS, mask))
    return entries


def print_info(label, uid):
    info = None
    for _ in range(3):
        info = master.nfcp.request_info_for_uid(uid, 1200)
        if info is not None:
            break
        master.time.sleep_ms(120)

    if info is None:
        print(label, "0x%08X" % uid, "NO_INFO")
        return None

    print(
        label,
        "0x%08X" % uid,
        info.get("app_name"),
        info.get("app_version"),
        info.get("script_status"),
    )
    return info


def print_stats(label, stats):
    fields = (
        "tx_ok",
        "rx_ok",
        "rx_timeout",
        "rx_duplicate",
        "rx_crc_error",
        "last_rssi",
        "channel",
    )
    parts = []
    for name in fields:
        parts.append("%s=%s" % (name, stats.get(name, "--")))
    print(label, " ".join(parts))


master.gui.ensure_bridge_channel()
master.gui.modules = master.nfcp.discover(4500)
master.gui.load_modules_from_db()
uids = master.gui.get_module_uids()
print("MODULE_COUNT", len(uids))
print("UIDS", ["0x%08X" % uid for uid in uids])

if not uids:
    raise RuntimeError("No modules discovered")

stats_before = master.get_local_radio_stats()
print_stats("STATS_BEFORE", stats_before)

loaded_uids = []
for index, uid in enumerate(uids):
    entries = make_entries(index)
    ok, code = master.nfcp.script_clear_status_for_uid(uid, SCRIPT_ID, master.SCRIPT_LOAD_TIMEOUT_MS)
    print("CLEAR", "0x%08X" % uid, ok, code)
    if not ok:
        continue

    ok, code = master.nfcp.script_load_status_for_uid(uid, SCRIPT_ID, entries, master.SCRIPT_LOAD_TIMEOUT_MS)
    print("LOAD", "0x%08X" % uid, ok, code, "entries", len(entries))
    if not ok:
        continue

    loaded_uids.append(uid)

if not loaded_uids:
    raise RuntimeError("No modules loaded")

for uid in loaded_uids:
    ok = master.nfcp.arm_for_uid(uid, 2000)
    print("ARM", "0x%08X" % uid, ok)

for _ in range(4):
    master.script_time_sync_broadcast()
    master.time.sleep_ms(40)

start_master_ms = (master.time.ticks_ms() + START_DELAY_MS) & 0x3FFFFFFF
started_uids = []
for uid in loaded_uids:
    ok, code = master.nfcp.script_start_status_for_uid(
        uid,
        SCRIPT_ID,
        START_DELAY_MS,
        0,
        2000,
        start_master_ms,
    )
    print("START", "0x%08X" % uid, ok, code)
    if ok:
        started_uids.append(uid)

master.nfcp.script_start_broadcast(SCRIPT_ID, START_DELAY_MS, 0, start_master_ms)
print("START_BROADCAST", "id=0x%04X" % SCRIPT_ID)

run_ms = ((ENTRY_COUNT - 1) * INTERVAL_MS) + START_DELAY_MS + 900
deadline = master.time.ticks_add(master.time.ticks_ms(), run_ms)
last_hb = master.time.ticks_ms()

while master.time.ticks_diff(deadline, master.time.ticks_ms()) > 0:
    now = master.time.ticks_ms()
    if master.time.ticks_diff(now, last_hb) >= HEARTBEAT_MS:
        master.nfcp.script_heartbeat_broadcast(SCRIPT_ID, master.gui.get_script_fire_elapsed_ms())
        last_hb = now
    master.lv.timer_handler()
    master.time.sleep_ms(5)

for uid in loaded_uids:
    print_info("INFO_AFTER", uid)

for uid in loaded_uids:
    ok = False
    for _ in range(3):
        ok = master.nfcp.disarm_for_uid(uid, 2000)
        if ok:
            break
        master.time.sleep_ms(120)
    print("DISARM", "0x%08X" % uid, ok)

stats_after = master.get_local_radio_stats()
print_stats("STATS_AFTER", stats_after)

try:
    tx_before = int(stats_before.get("tx_ok", 0))
    rx_before = int(stats_before.get("rx_ok", 0))
    timeout_before = int(stats_before.get("rx_timeout", 0))
    tx_after = int(stats_after.get("tx_ok", 0))
    rx_after = int(stats_after.get("rx_ok", 0))
    timeout_after = int(stats_after.get("rx_timeout", 0))
    print(
        "STATS_DELTA",
        "tx_ok", tx_after - tx_before,
        "rx_ok", rx_after - rx_before,
        "rx_timeout", timeout_after - timeout_before,
    )
except Exception as e:
    print("STATS_DELTA_ERR", e)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--script-id", type=lambda value: int(value, 0), default=0x5403)
    parser.add_argument("--entries", type=int, default=60)
    parser.add_argument("--interval-ms", type=int, default=10)
    parser.add_argument("--start-delay-ms", type=int, default=2000)
    parser.add_argument("--heartbeat-ms", type=int, default=250)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    code = CODE.format(
        script_id=args.script_id,
        entry_count=args.entries,
        interval_ms=args.interval_ms,
        start_delay_ms=args.start_delay_ms,
        heartbeat_ms=args.heartbeat_ms,
    )

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        try:
            port.dtr = False
            port.rts = False
            time.sleep(0.2)
        except Exception:
            pass

        port.reset_input_buffer()

        port.write(direct.CTRL_C)
        time.sleep(0.2)
        port.write(direct.CTRL_C)
        time.sleep(0.5)
        if port.in_waiting:
            sys.stdout.write(port.read(port.in_waiting).decode("utf-8", "replace"))
            sys.stdout.flush()

        port.write(direct.CTRL_A)
        direct.read_until(port, b"raw REPL; CTRL-B to exit\r\n>", 8)

        port.write(code.encode("utf-8"))
        port.write(direct.CTRL_D)
        response = direct.read_until(port, b"\x04\x04>", args.timeout)

        ok_at = response.find(b"OK")
        if ok_at < 0:
            raise RuntimeError("Raw REPL did not accept command: %r" % response[-300:])

        payload = response[ok_at + 2:]
        first_eof = payload.find(direct.CTRL_D)
        second_eof = payload.find(direct.CTRL_D, first_eof + 1)
        if first_eof < 0 or second_eof < 0:
            raise RuntimeError("Incomplete raw response: %r" % response[-300:])

        normal = payload[:first_eof]
        error = payload[first_eof + 1:second_eof]

        sys.stdout.write(normal.decode("utf-8", "replace"))
        sys.stdout.flush()

        if error:
            raise RuntimeError(error.decode("utf-8", "replace"))

        port.write(direct.CTRL_B)
        time.sleep(0.2)


if __name__ == "__main__":
    main()
