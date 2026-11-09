from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts/lib")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

SCRIPT_ID = {script_id}
ENTRY_COUNT = {entry_count}
CHUNK_SIZE = {chunk_size}

master.gui.ensure_bridge_channel()
master.clear_local_radio_stats()
master.gui.modules = master.nfcp.discover()
master.gui.load_modules_from_db()
print("MODULE_COUNT", len(master.gui.modules))

uids = master.gui.get_module_uids()
print("UIDS", ["0x%08X" % uid for uid in uids])

entries = []
for i in range(ENTRY_COUNT):
    entries.append((i * 10, 0x0001 << (i % 4)))

for uid in uids:
    print("BEGIN_UID", "0x%08X" % uid)
    ok, code = master.nfcp.script_clear_status_for_uid(uid, SCRIPT_ID)
    print("CLEAR", ok, code)
    if not ok:
        continue

    chunk_index = 0
    offset = 0
    while offset < len(entries):
        chunk = entries[offset:offset + CHUNK_SIZE]
        ok, code = master.send_with_ack_detail(
            uid,
            master.nfcp.CMD_SCRIPT_LOAD,
            lambda uid=uid, chunk_index=chunk_index, chunk=chunk: master.nfcp_tx.build_script_load(
                uid,
                SCRIPT_ID,
                chunk_index,
                chunk
            ),
            3000,
            attempts=2,
        )
        print("CHUNK", "0x%08X" % uid, chunk_index, offset, len(chunk), ok, code)
        if not ok:
            break
        offset += len(chunk)
        chunk_index += 1

print("STATS", master.get_local_radio_stats(1200))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--script-id", type=lambda value: int(value, 0), default=0x5303)
    parser.add_argument("--entries", type=int, default=160)
    parser.add_argument("--chunk-size", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=150.0)
    args = parser.parse_args()

    code = CODE.format(
        script_id=args.script_id,
        entry_count=args.entries,
        chunk_size=args.chunk_size,
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
