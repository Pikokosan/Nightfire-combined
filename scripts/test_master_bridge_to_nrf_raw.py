from __future__ import print_function

import argparse
import sys
import threading
import time

import serial

sys.path.insert(0, "main")
sys.path.insert(0, "scripts")

import bridge_radio
import raw_micropython_upload as raw


DUMMY_RX_CODE = '''
import nrf_radio
nrf_radio.init(channel={channel}, tx_power=8, address={address})
p = nrf_radio.recv(5000)
print("RX", p, nrf_radio.last_rssi())
'''


MASTER_SEND_CODE = r'''
from machine import UART, Pin
import bridge_radio
import time

uart = UART(2, baudrate=256000, tx=Pin(15), rx=Pin(16))
parser = bridge_radio.BridgeFrameParser()

def read_one(timeout_ms=3000):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        if uart.any():
            for b in uart.read(64) or b"":
                frame = parser.feed(b)
                if frame:
                    return frame
        else:
            time.sleep_ms(5)
    return None

payload = bytes({payload!r})
uart.write(bridge_radio.build_frame(0x43, bridge_radio.CMD_SET_CHANNEL, bytes([{channel}])))
frame = read_one()
if frame:
    print("SET_CHANNEL", frame.cmd, frame.flags, bytes(frame.payload))
else:
    print("SET_CHANNEL_TIMEOUT")
uart.write(bridge_radio.build_frame(0x44, bridge_radio.CMD_SEND_NFCP, payload))
frame = read_one()
if frame:
    print("MASTER_RX", frame.cmd, frame.flags, bytes(frame.payload))
else:
    print("MASTER_TIMEOUT")
'''


def run_rx(port_name, channel, address, result):
    try:
        with serial.Serial(port_name, 115200, timeout=0.05, write_timeout=2) as port:
            raw.stop_running_program(port)
            raw.enter_raw_repl(port)
            code = DUMMY_RX_CODE.format(channel=channel, address=address)
            result["out"] = raw.exec_raw(port, code, timeout=8)
            raw.exit_raw_repl(port)
            port.write(raw.CTRL_D)
            time.sleep(0.5)
    except Exception as exc:
        result["err"] = repr(exc)


def run_master(master_port, payload, channel):
    with serial.Serial(master_port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            code = MASTER_SEND_CODE.format(payload=payload, channel=channel)
            return raw.exec_raw(port, code, timeout=8)
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
    parser.add_argument("--channel", type=int, default=90)
    parser.add_argument("--address", default="0x67676767")
    parser.add_argument("--network", default="0x04D2")
    args = parser.parse_args()

    address = int(args.address, 0)
    network = int(args.network, 0) & 0xFFFF
    payload = network.to_bytes(2, "big") + b"master-bridge-test"

    rx_result = {}
    thread = threading.Thread(
        target=run_rx,
        args=(args.dummy, args.channel, address, rx_result),
    )
    thread.start()
    time.sleep(0.8)

    master_out = run_master(args.master, payload, args.channel)
    thread.join(9)

    print("MASTER:", repr(master_out))
    print("DUMMY:", rx_result)


if __name__ == "__main__":
    main()
