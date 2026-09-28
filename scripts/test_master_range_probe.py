from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

master.gui.ensure_bridge_channel()
try:
    master.clear_local_radio_stats(300)
except Exception as e:
    print("CLEAR_STATS_ERROR", e)

devices = master.discover()
print("DISCOVER_COUNT", len(devices))

for dev in devices:
    uid_text = dev.get("unique_id", "0x00000000")
    try:
        uid = int(uid_text, 16) & 0xFFFFFFFF
    except:
        print("BAD_UID", uid_text)
        continue

    ok = 0
    lost = 0
    total = 0
    last = None

    for _ in range({count}):
        ms = master.request_ping_for_uid(uid, {timeout_ms})
        if ms is None:
            lost += 1
        else:
            ok += 1
            total += int(ms)
            last = int(ms)
        master.time.sleep_ms({interval_ms})

    avg = (total // ok) if ok else None
    print(
        "PING",
        uid_text,
        "CH",
        dev.get("group_id", "--"),
        "ok",
        ok,
        "lost",
        lost,
        "avg",
        avg,
        "last",
        last,
        "link",
        master.get_last_radio_link(uid_text),
    )

print("STATS", master.get_local_radio_stats(500))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout-ms", type=int, default=300)
    parser.add_argument("--interval-ms", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=80.0)
    args = parser.parse_args()

    code = CODE.format(
        count=args.count,
        timeout_ms=args.timeout_ms,
        interval_ms=args.interval_ms,
    )

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, code, timeout=args.timeout).decode("utf-8", "replace")
            sys.stdout.write(output)
            sys.stdout.flush()
        finally:
            try:
                raw.exit_raw_repl(port)
            except Exception:
                pass
            try:
                port.write(raw.CTRL_D)
                time.sleep(0.5)
            except Exception:
                pass


if __name__ == "__main__":
    main()
