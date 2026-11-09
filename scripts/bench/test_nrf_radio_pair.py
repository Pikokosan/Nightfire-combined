import argparse
import threading
import time

import serial


CTRL_A = b"\x01"
CTRL_B = b"\x02"
CTRL_C = b"\x03"
CTRL_D = b"\x04"


def read_available(port, seconds=0.2):
    end = time.time() + seconds
    out = bytearray()
    while time.time() < end:
        waiting = port.in_waiting
        if waiting:
            out.extend(port.read(waiting))
            end = time.time() + seconds
        else:
            time.sleep(0.02)
    return bytes(out)


def wait_for(port, marker, timeout=8):
    end = time.time() + timeout
    out = bytearray()
    while time.time() < end:
        waiting = port.in_waiting
        if waiting:
            out.extend(port.read(waiting))
            if marker in out:
                return bytes(out)
        else:
            time.sleep(0.02)
    raise RuntimeError("timeout waiting for %r, got %r" % (marker, bytes(out[-500:])))


def stop_running_program(port):
    port.dtr = True
    out = bytearray()
    for _ in range(12):
        port.write(CTRL_C)
        time.sleep(0.1)
        out.extend(read_available(port, 0.05))
        if b">>>" in out:
            return bytes(out)
    port.write(b"\r")
    time.sleep(0.2)
    out.extend(read_available(port, 0.5))
    return bytes(out)


def enter_raw_repl(port):
    port.write(CTRL_A)
    return wait_for(port, b"raw REPL; CTRL-B to exit\r\n>", timeout=5)


def exit_raw_repl(port):
    port.write(CTRL_B)
    return read_available(port, 0.2)


def exec_raw(port, code, timeout=10):
    port.write(code.encode("utf-8"))
    port.write(CTRL_D)
    response = wait_for(port, b"\x04\x04>", timeout=timeout)
    ok_at = response.find(b"OK")
    if ok_at < 0:
        raise RuntimeError("raw REPL rejected command: %r" % response[-500:])

    payload = response[ok_at + 2:]
    first_eof = payload.find(CTRL_D)
    second_eof = payload.find(CTRL_D, first_eof + 1)
    normal = payload[:first_eof]
    error = payload[first_eof + 1:second_eof]
    if error:
        raise RuntimeError(error.decode("utf-8", "replace"))
    return normal


def soft_reset(port):
    port.write(CTRL_D)
    time.sleep(0.5)
    return read_available(port, 1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rx", default="COM13")
    parser.add_argument("--tx", default="COM15")
    parser.add_argument("--channel", type=int, default=90)
    parser.add_argument("--address", default="0x67676767")
    parser.add_argument("--message", default="nightfire-test")
    args = parser.parse_args()

    address = int(args.address, 0)

    rx = serial.Serial(args.rx, 115200, timeout=0.1, write_timeout=1)
    tx = serial.Serial(args.tx, 115200, timeout=0.1, write_timeout=1)

    try:
        print("Stopping apps...")
        print(args.rx, repr(stop_running_program(rx)[-160:]))
        print(args.tx, repr(stop_running_program(tx)[-160:]))

        enter_raw_repl(rx)
        enter_raw_repl(tx)

        rx_result = {}

        def receive():
            try:
                rx_result["out"] = exec_raw(
                    rx,
                    (
                        "import nrf_radio\n"
                        "nrf_radio.init(channel=%d, tx_power=8, address=%d)\n"
                        "p = nrf_radio.recv(5000)\n"
                        "print('RX', p, nrf_radio.last_rssi())\n"
                    ) % (args.channel, address),
                    timeout=8,
                )
            except Exception as exc:
                rx_result["err"] = repr(exc)

        thread = threading.Thread(target=receive)
        thread.start()
        time.sleep(0.6)

        tx_out = exec_raw(
            tx,
            (
                "import nrf_radio\n"
                "nrf_radio.init(channel=%d, tx_power=8, address=%d)\n"
                "nrf_radio.send(%r)\n"
                "print('TX done')\n"
            ) % (args.channel, address, args.message.encode("utf-8")),
            timeout=5,
        )
        thread.join(8)

        print("TX:", repr(tx_out))
        print("RX:", rx_result)

        exit_raw_repl(rx)
        exit_raw_repl(tx)
        print("Resetting apps...")
        print(args.rx, repr(soft_reset(rx)[-300:]))
        print(args.tx, repr(soft_reset(tx)[-300:]))
    finally:
        rx.close()
        tx.close()


if __name__ == "__main__":
    main()
