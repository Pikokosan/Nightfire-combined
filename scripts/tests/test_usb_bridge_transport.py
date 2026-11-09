import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


source = Path(__file__).resolve().parents[2] / "main/usb_bridge_transport.py"
spec = importlib.util.spec_from_file_location("usb_bridge_transport_test", source)
transport_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport_module)


class FakePoll:
    def register(self, stream, flags):
        self.stream = stream

    def poll(self, timeout):
        return [1] if self.stream.tell() < len(self.stream.getvalue()) else []


class ByteStdin:
    """stdin with a byte .buffer, like MicroPython's."""

    def __init__(self, data):
        self.buffer = io.BytesIO(data)

    def tell(self):
        return self.buffer.tell()

    def getvalue(self):
        return self.buffer.getvalue()


def reference_decode(data):
    """What the transport must deliver: hex payloads of NFUSB1 lines."""
    out = bytearray()
    for line in data.replace(b"\r", b"\n").split(b"\n")[:-1]:
        if len(line) > 512 or not line.startswith(b"NFUSB1 "):
            continue
        try:
            out += bytes.fromhex(line[7:].decode("ascii"))
        except ValueError:
            pass
    return bytes(out)


class UsbTransportTests(unittest.TestCase):
    def decode(self, text, chunk=2, binary=False):
        stdin = ByteStdin(text.encode()) if binary else io.StringIO(text)
        with patch.object(sys, "stdin", stdin), patch.object(
            transport_module, "select", SimpleNamespace(poll=FakePoll, POLLIN=1)
        ):
            transport = transport_module.UsbHexTransport()
            result = bytearray()
            while transport.any():
                result.extend(transport.read(chunk))
            return bytes(result)

    def test_byte_stdin_matches_text_stdin(self):
        text = "Console\r\nNFUSB1 aa55000103ff\r\nNFUSB1 zz\n" + "y" * 700 + "\nNFUSB1 0a0b\n"
        self.assertEqual(self.decode(text, binary=True), self.decode(text))
        self.assertEqual(self.decode(text, binary=True), bytes.fromhex("aa55000103ff0a0b"))

    def test_long_line_is_read_in_one_call(self):
        payload = bytes(range(200))
        line = "NFUSB1 " + payload.hex() + "\n"
        with patch.object(sys, "stdin", ByteStdin(line.encode())), patch.object(
            transport_module, "select", SimpleNamespace(poll=FakePoll, POLLIN=1)
        ):
            transport = transport_module.UsbHexTransport()
            self.assertEqual(bytes(transport.read(512)), payload)

    def test_partial_line_waits_for_the_rest(self):
        with patch.object(transport_module, "select", SimpleNamespace(poll=FakePoll, POLLIN=1)):
            stdin = ByteStdin(b"NFUSB1 0102")
            with patch.object(sys, "stdin", stdin):
                transport = transport_module.UsbHexTransport()
                self.assertEqual(bytes(transport.read(64)), b"")
                position = stdin.buffer.tell()     # the rest of the line arrives
                stdin.buffer.seek(0, 2)
                stdin.buffer.write(b"03\n")
                stdin.buffer.seek(position)
                self.assertEqual(bytes(transport.read(64)), b"\x01\x02\x03")

    def test_random_streams_match_reference(self):
        import random
        rng = random.Random(7)
        pieces = [b"NFUSB1 %s" % bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40))).hex().encode(),
                  b"NFUSB1 abc", b"noise", b"NFUSB1 " + b"0" * 600, b"NFUSB1 "]
        for trial in range(200):
            data = b"".join(rng.choice(pieces) + rng.choice([b"\n", b"\r\n"]) for _ in range(rng.randrange(1, 8)))
            for binary in (True, False):
                self.assertEqual(self.decode(data.decode(), chunk=rng.randrange(1, 64), binary=binary),
                                 reference_decode(data), (trial, binary))

    def test_console_noise_and_fragmented_reads(self):
        self.assertEqual(self.decode("Console\r\nNFUSB1 aa55000103ff\r\n"), bytes.fromhex("aa55000103ff"))

    def test_bad_and_oversized_lines_recover(self):
        text = "NFUSB1 zz\nNFUSB1 f\n" + "x" * 600 + "\nNFUSB1 010203\n"
        self.assertEqual(self.decode(text), b"\x01\x02\x03")

    def test_write_is_ascii_and_keeps_control_bytes_out_of_console(self):
        output = io.StringIO()
        with patch.object(sys, "stdin", io.StringIO()), patch.object(
            sys, "stdout", output
        ), patch.object(transport_module, "select", SimpleNamespace(poll=FakePoll, POLLIN=1)):
            transport = transport_module.UsbHexTransport()
            self.assertEqual(transport.write(b"\x03\x04\xff"), 3)
        self.assertEqual(output.getvalue(), "NFUSB1 0304ff\n")


if __name__ == "__main__":
    unittest.main()
