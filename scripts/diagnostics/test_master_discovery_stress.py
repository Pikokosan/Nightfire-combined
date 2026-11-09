import argparse
import sys

import serial

sys.path.insert(0, "scripts/lib")
import raw_micropython_upload as raw


CODE_TEMPLATE = r'''
import __main__ as master

expected = {expected!r}
seen = {{}}
misses = {{}}
for uid in expected:
    seen[uid] = 0
    misses[uid] = 0

for i in range({runs}):
    devices = master.discover(idle_ms={scan_ms}, max_scan_ms={max_scan_ms}, collect_info=False)
    found = []
    for dev in devices:
        uid = dev.get("unique_id", "")
        if uid:
            found.append(uid)
            if uid in seen:
                seen[uid] += 1
    for uid in expected:
        if uid not in found:
            misses[uid] += 1
    print("RUN", i + 1, "COUNT", len(devices), "FOUND", ",".join(found))

print("SUMMARY", seen, misses)
try:
    print("BRIDGE_STATS", master.get_local_radio_stats())
except Exception as e:
    print("BRIDGE_STATS_ERROR", e)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--runs", type=int, default=12)
    parser.add_argument("--scan-ms", type=int, default=900)
    parser.add_argument("--max-scan-ms", type=int, default=1800)
    parser.add_argument("--expected", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    code = CODE_TEMPLATE.format(
        runs=args.runs,
        scan_ms=args.scan_ms,
        max_scan_ms=args.max_scan_ms,
        expected=args.expected,
    )

    with serial.Serial(args.master, args.baud, timeout=0.05, write_timeout=2) as port:
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
                import time
                time.sleep(0.5)
            except Exception:
                pass


if __name__ == "__main__":
    main()
