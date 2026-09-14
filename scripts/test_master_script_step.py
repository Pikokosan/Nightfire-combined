from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

SCRIPT_ID = {script_id}

master.gui.ensure_bridge_channel()
master.gui.modules = master.nfcp.discover()
master.gui.load_modules_from_db()
print("MODULE_COUNT", len(master.gui.modules))

uids = master.gui.get_module_uids()
print("UIDS", ["0x%08X" % uid for uid in uids])

for uid in uids:
    ok = master.nfcp.arm_for_uid(uid)
    print("ARM", "0x%08X" % uid, ok)

for uid in uids:
    ok, code = master.nfcp.script_clear_status_for_uid(uid, SCRIPT_ID)
    print("CLEAR", "0x%08X" % uid, ok, code)
    if not ok:
        continue
    ok = master.nfcp.script_load_for_uid(uid, SCRIPT_ID, [(10, 0x0001), (20, 0x0002)])
    print("LOAD", "0x%08X" % uid, ok)
    if not ok:
        continue
    master.nfcp.script_step_for_uid(uid, SCRIPT_ID, 10)
    print("STEP_SENT", "0x%08X" % uid)
    master.time.sleep_ms(40)

for uid in uids:
    ok = master.nfcp.disarm_for_uid(uid)
    print("DISARM", "0x%08X" % uid, ok)

print("STATS", master.get_local_radio_stats())
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--script-id", type=lambda value: int(value, 0), default=0x5301)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()

    code = CODE.format(script_id=args.script_id)

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
