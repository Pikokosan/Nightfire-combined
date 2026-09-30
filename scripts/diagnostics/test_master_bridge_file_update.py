from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import __main__ as master

path = "bridge_radio.py"
remote_name = "bridge_radio.py"
size, crc = master.bridge_update_file_crc(path)
name = remote_name.encode()
payload = bytes([len(name)]) + name + size.to_bytes(4, "big") + crc.to_bytes(2, "big")

frame = master.local_radio_command(master.bridge_radio.CMD_FILE_BEGIN, payload, 1500)
print("BEGIN", None if not frame else (frame.cmd, frame.flags, bytes(frame.payload)))

offset = 0
with open(path, "rb") as f:
    while True:
        data = f.read(master.BRIDGE_UPDATE_CHUNK_SIZE)
        if not data:
            break
        frame = master.local_radio_command(
            master.bridge_radio.CMD_FILE_WRITE,
            offset.to_bytes(4, "big") + data,
            1500
        )
        print("WRITE", offset, None if not frame else (frame.cmd, frame.flags, bytes(frame.payload)))
        if not frame or frame.flags & master.bridge_radio.FLAG_ERROR:
            break
        offset += len(data)
    else:
        pass

if offset == size:
    frame = master.local_radio_command(master.bridge_radio.CMD_FILE_END, b"", 2000)
    print("END", None if not frame else (frame.cmd, frame.flags, bytes(frame.payload)))
    if frame and not (frame.flags & master.bridge_radio.FLAG_ERROR):
        print("RESET", master.reset_local_radio(500))
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    with serial.Serial(args.master, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=args.timeout).decode("utf-8", "replace")
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
