from __future__ import print_function

import argparse
import sys
import threading
import time

import serial

sys.path.insert(0, "main")
sys.path.insert(0, "scripts")

import raw_micropython_upload as raw


DUMMY_NOISE_CODE = '''
import nrf_radio
nrf_radio.init(channel={channel}, tx_power=8, address={address})
for i in range(8):
    s = nrf_radio.noise_sample({channel}, duration_ms=250, interval_ms=2, busy_dbm=-90)
    print("NOISE", i, s)
'''


MASTER_BURST_CODE = r'''
from machine import UART, Pin
import e77_radio
import time

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = e77_radio.E77FrameParser()

def read_one(timeout_ms=500):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline,time.ticks_ms()) > 0:
        if uart.any():
            for b in uart.read(64) or b"":
                f = parser.feed(b)
                if f:
                    return f
        else:
            time.sleep_ms(2)
    return None

uart.write(e77_radio.build_frame(1, e77_radio.CMD_SET_CHANNEL, bytes([{channel}])))
print("SET", read_one())
payload = bytes({payload!r})
for i in range(5):
    uart.write(e77_radio.build_frame((i + 2) & 0xFF, e77_radio.CMD_SEND_NFCP, payload))
    f = read_one()
    if f:
        print("BURST", i, f.cmd, f.flags, bytes(f.payload))
    else:
        print("BURST", i, "timeout")
    time.sleep_ms(30)
'''


def run_dummy(dummy_port, channel, address, result):
    try:
        with serial.Serial(dummy_port, 115200, timeout=0.05, write_timeout=2) as port:
            raw.stop_running_program(port)
            raw.enter_raw_repl(port)
            code = DUMMY_NOISE_CODE.format(channel=channel, address=address)
            result["out"] = raw.exec_raw(port, code, timeout=8)
            raw.exit_raw_repl(port)
            port.write(raw.CTRL_D)
            time.sleep(0.5)
    except Exception as exc:
        result["err"] = repr(exc)


def run_master(master_port, channel, payload):
    with serial.Serial(master_port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            code = MASTER_BURST_CODE.format(channel=channel, payload=payload)
            return raw.exec_raw(port, code, timeout=12)
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--master", default="COM21")
    parser.add_argument("--dummy", default="COM16")
    parser.add_argument("--channel", type=int, default=40)
    parser.add_argument("--address", default="0x67676767")
    parser.add_argument("--network", default="0x04D2")
    args = parser.parse_args()

    address = int(args.address, 0)
    network = int(args.network, 0) & 0xFFFF
    payload = network.to_bytes(2, "big") + b"x" * 80

    dummy_result = {}
    thread = threading.Thread(
        target=run_dummy,
        args=(args.dummy, args.channel, address, dummy_result),
    )
    thread.start()
    time.sleep(0.3)
    try:
        master_out = run_master(args.master, args.channel, payload)
    except Exception as exc:
        master_out = "ERROR: %r" % (exc,)
    thread.join(10)

    print("MASTER:", repr(master_out))
    print("DUMMY:", dummy_result)


if __name__ == "__main__":
    main()
