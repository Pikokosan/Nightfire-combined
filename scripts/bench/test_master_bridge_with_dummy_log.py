from __future__ import print_function

import argparse
import sys
import threading
import time

import serial

sys.path.insert(0, "scripts/lib")
import raw_micropython_upload as raw


MASTER_CODE = r'''
from machine import UART, Pin
import time
import bridge_radio
import nfcp

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = bridge_radio.BridgeFrameParser()

def read_frames(timeout_ms=7000):
    frames = []
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            data = uart.read(64) or b""
            for b in data:
                frame = parser.feed(b)
                if frame:
                    frames.append(frame)
        else:
            time.sleep_ms(5)
    return frames

n = nfcp.NFCP(1)
stream = nfcp.NFCPStreamParser()

seq = 0x31
uart.write(bridge_radio.build_frame(seq, bridge_radio.CMD_PING))
frames = read_frames(1000)
print("PING_FRAMES", [(f.seq, f.cmd, f.flags, bytes(f.payload)) for f in frames])

seq = 0x52
uart.write(bridge_radio.build_send_nfcp(seq, n.build_discover_broadcast(0x9ABC)))
frames = read_frames(7000)
print("DISC_FRAMES", [(f.seq, f.cmd, f.flags, len(f.payload), bytes(f.payload[:8])) for f in frames])
for f in frames:
    if f.cmd == bridge_radio.CMD_NFCP_RX:
        rx = bridge_radio.parse_nfcp_rx(f.payload)
        print("RADIO_LINK", rx.get("rssi"), rx.get("snr"), len(rx.get("nfcp_frame", b"")))
        for b in rx.get("nfcp_frame", b""):
            pkt = stream.feed(b)
            if pkt:
                print("NFCP_PKT", pkt)
'''


def read_dummy(port_name, seconds):
    try:
        with serial.Serial(port_name, 115200, timeout=0.05, write_timeout=1) as port:
            end = time.time() + seconds
            while time.time() < end:
                waiting = port.in_waiting
                if waiting:
                    data = port.read(waiting)
                    sys.stdout.write("DUMMY_LOG " + data.decode("utf-8", "replace"))
                    sys.stdout.flush()
                time.sleep(0.02)
    except Exception as exc:
        print("DUMMY_LOG_ERROR", exc)


def run_master(master_port):
    with serial.Serial(master_port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, MASTER_CODE, timeout=15).decode("utf-8", "replace")
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
                data = port.read(port.in_waiting or 1)
                if data:
                    sys.stdout.write(data.decode("utf-8", "replace"))
                    sys.stdout.flush()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--dummy", default="COM16")
    args = parser.parse_args()

    reader = threading.Thread(target=read_dummy, args=(args.dummy, 10))
    reader.daemon = True
    reader.start()
    time.sleep(0.5)
    run_master(args.master)
    reader.join()


if __name__ == "__main__":
    main()
