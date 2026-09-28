from __future__ import print_function

import argparse
import sys
import threading
import time

import serial

sys.path.insert(0, "main")
sys.path.insert(0, "scripts")

import raw_micropython_upload as raw


MASTER_RX_CODE = r'''
from machine import UART, Pin
import bridge_radio
import time

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = bridge_radio.BridgeFrameParser()
deadline = time.ticks_add(time.ticks_ms(), 6000)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    if uart.any():
        for b in uart.read(64) or b"":
            frame = parser.feed(b)
            if frame:
                print("MASTER_RX", frame.cmd, frame.flags, bytes(frame.payload))
    else:
        time.sleep_ms(5)
'''


DUMMY_TX_CODE = '''
import nrf_radio
nrf_radio.init(channel={channel}, tx_power=8, address={address})
nrf_radio.send({payload!r})
print("DUMMY_TX_DONE", nrf_radio.stats())
'''


def run_master(master_port, result):
    try:
        with serial.Serial(master_port, 115200, timeout=0.05, write_timeout=2) as port:
            raw.stop_running_program(port)
            raw.enter_raw_repl(port)
            result["out"] = raw.exec_raw(port, MASTER_RX_CODE, timeout=9)
            raw.exit_raw_repl(port)
            port.write(raw.CTRL_D)
            time.sleep(0.5)
    except Exception as exc:
        result["err"] = repr(exc)


def run_dummy(dummy_port, channel, address, payload):
    with serial.Serial(dummy_port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            code = DUMMY_TX_CODE.format(
                channel=channel,
                address=address,
                payload=payload,
            )
            return raw.exec_raw(port, code, timeout=5)
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
    payload = network.to_bytes(2, "big") + b"dummy-to-master-test"

    master_result = {}
    thread = threading.Thread(target=run_master, args=(args.master, master_result))
    thread.start()
    time.sleep(0.8)

    dummy_out = run_dummy(args.dummy, args.channel, address, payload)
    thread.join(10)

    print("DUMMY:", repr(dummy_out))
    print("MASTER:", master_result)


if __name__ == "__main__":
    main()
