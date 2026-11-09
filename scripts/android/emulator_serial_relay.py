import argparse
import select
import socket
import time

import serial


def open_port(name, deadline=None):
    """Wait for the radio's COM port; it disappears while the bridge resets."""
    while True:
        try:
            return serial.Serial(name, 115200, timeout=0.01, write_timeout=1)
        except serial.SerialException:
            if deadline is not None and time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def relay(listener, port):
    client, address = listener.accept()
    print("Emulator relay connected", address, flush=True)
    with client:
        client.settimeout(1)
        while True:
            if select.select([client], [], [], 0)[0]:
                data = client.recv(4096)
                if not data:
                    break
                port.write(data)
            data = port.read(port.in_waiting or 1)
            if data:
                client.sendall(data)
    print("Emulator relay disconnected", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--listen-port", type=int, default=8765)
    options = parser.parse_args()
    port = open_port(options.port, time.monotonic() + 30)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", options.listen_port))
        listener.listen(1)
        print("Local-only relay 127.0.0.1:%d -> %s" % (options.listen_port, options.port), flush=True)
        while True:
            try:
                relay(listener, port)
            except (ConnectionError, socket.timeout) as error:
                print("Relay client closed:", error, flush=True)
            except (serial.SerialException, OSError) as error:
                # Bridge reset (e.g. after a firmware update): drop the app's
                # connection so it reconnects, then wait for the port again.
                print("Radio port lost:", error, flush=True)
                try:
                    port.close()
                except Exception:
                    pass
                time.sleep(1.0)
                port = open_port(options.port)
                print("Radio port reopened", flush=True)


if __name__ == "__main__":
    main()
