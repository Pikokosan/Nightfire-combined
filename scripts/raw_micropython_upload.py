import argparse
import os
import sys
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


def wait_for(port, marker, timeout=10):
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

    raise RuntimeError("Timed out waiting for %r. Last output: %r" % (marker, bytes(out[-500:])))


def stop_running_program(port, timeout=12):
    end = time.time() + timeout
    out = bytearray()

    while time.time() < end:
        port.write(CTRL_C)
        time.sleep(0.12)
        chunk = read_available(port, 0.05)
        if chunk:
            out.extend(chunk)
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
            if b">>>" in out:
                return

    port.write(b"\r\n")
    time.sleep(0.3)
    out.extend(read_available(port, 0.3))
    if b">>>" not in out:
        raise RuntimeError("Could not stop the running program and reach the MicroPython prompt.")


def enter_raw_repl(port):
    port.write(CTRL_A)
    wait_for(port, b"raw REPL; CTRL-B to exit\r\n>", timeout=5)


def exit_raw_repl(port):
    port.write(CTRL_B)
    read_available(port, 0.2)


def exec_raw(port, code, timeout=10):
    if isinstance(code, str):
        code = code.encode("utf-8")

    port.write(code)
    port.write(CTRL_D)

    response = wait_for(port, b"\x04\x04>", timeout=timeout)
    ok_at = response.find(b"OK")

    if ok_at < 0:
        raise RuntimeError("Raw REPL did not accept command: %r" % response[-500:])

    payload = response[ok_at + 2:]
    first_eof = payload.find(CTRL_D)
    second_eof = payload.find(CTRL_D, first_eof + 1)

    if first_eof < 0 or second_eof < 0:
        raise RuntimeError("Raw REPL response was incomplete: %r" % response[-500:])

    normal = payload[:first_eof]
    error = payload[first_eof + 1:second_eof]

    if error:
        raise RuntimeError(error.decode("utf-8", "replace"))

    return normal


def byte_literal(data):
    return repr(data)


def upload_file(port, local_path, remote_path, chunk_size=768):
    size = os.path.getsize(local_path)
    print("Uploading %s -> %s (%d bytes)" % (local_path, remote_path, size))

    exec_raw(port, "f = open(%r, 'wb')\nf.close()\n" % remote_path)

    with open(local_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break

            code = (
                "f = open(%r, 'ab')\n"
                "f.write(%s)\n"
                "f.close()\n"
            ) % (remote_path, byte_literal(chunk))
            exec_raw(port, code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("copies", nargs="+", help="Pairs of local_path:remote_path")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.05, write_timeout=2) as port:
        port.reset_input_buffer()
        stop_running_program(port)
        enter_raw_repl(port)

        try:
            for copy in args.copies:
                local_path, remote_path = copy.split(":", 1)
                upload_file(port, local_path, remote_path)

            print("Resetting device...")
            try:
                exec_raw(port, "import machine\nmachine.reset()\n", timeout=3)
            except Exception:
                pass
        finally:
            try:
                exit_raw_repl(port)
            except Exception:
                pass


if __name__ == "__main__":
    main()
