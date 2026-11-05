import argparse
import select
import socket
import time

import serial


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
    deadline = time.monotonic() + 30
    while True:
        try:
            port = serial.Serial(options.port, 115200, timeout=0.01, write_timeout=1)
            break
        except serial.SerialException:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    with port, socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", options.listen_port))
        listener.listen(1)
        print("Local-only relay 127.0.0.1:%d -> %s" % (options.listen_port, options.port), flush=True)
        while True:
            try:
                relay(listener, port)
            except (ConnectionError, socket.timeout) as error:
                print("Relay client closed:", error, flush=True)


if __name__ == "__main__":
    main()
