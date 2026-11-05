import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


source = Path(__file__).resolve().parents[1] / "main/usb_bridge_transport.py"
spec = importlib.util.spec_from_file_location("usb_bridge_transport_test", source)
transport_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport_module)


class FakePoll:
    def register(self, stream, flags):
        self.stream = stream

    def poll(self, timeout):
        return [1] if self.stream.tell() < len(self.stream.getvalue()) else []


class UsbTransportTests(unittest.TestCase):
    def decode(self, text):
        with patch.object(sys, "stdin", io.StringIO(text)), patch.object(
            transport_module, "select", SimpleNamespace(poll=FakePoll, POLLIN=1)
        ):
            transport = transport_module.UsbHexTransport()
            result = bytearray()
            while transport.any():
                result.extend(transport.read(2))
            return bytes(result)

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
