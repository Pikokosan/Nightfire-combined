import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'main'))
import nfcp
from command_interface import CommandInterface
from usb_monitor_nfcp import MasterKeepalive, keepalive_frame
from usb_monitor_nfcp import decode_query


class KeepaliveTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.packets = []
        self.success = True
        self.interface = CommandInterface(SimpleNamespace(), clock=lambda: self.now,
                                          difference=lambda a, b: a - b)
        self.sender = MasterKeepalive(0x04D2, self.send, lambda: self.now, lambda a, b: a - b)

    def send(self, packet):
        self.packets.append(packet)
        return self.success

    def poll(self, ready=True):
        self.sender.poll(self.interface.session_state() == 1, ready)

    def test_frame_matches_original_master_including_escaping_and_clock_wrap(self):
        protocol = nfcp.NFCP(1)
        for tick in (0, 2500, 0xFFFF, 0xFFFFFFFF, 0x100000001):
            frame = keepalive_frame(tick)
            self.assertEqual(frame, protocol.build_keepalive_broadcast(tick))
            self.assertIsNone(decode_query(frame))  # USB callers still cannot send arbitrary keepalives.

    def test_interval_active_host_and_expiry(self):
        self.poll()
        self.assertFalse(self.packets)
        self.interface.heartbeat(b'TEST')
        self.poll()
        self.now = 2499
        self.poll()
        self.assertEqual(len(self.packets), 1)
        self.now = 2500
        self.poll()
        self.assertEqual(len(self.packets), 2)
        self.now = 3001
        self.poll()
        self.now = 10000
        self.poll()
        self.assertEqual(len(self.packets), 2)
        self.assertEqual(self.sender.payload()[1], 0)
        self.interface.heartbeat(b'NEW!')
        self.poll()
        self.assertEqual(len(self.packets), 3)

    def test_radio_down_stops_and_recovery_sends_immediately(self):
        self.interface.heartbeat(b'TEST')
        self.poll(False)
        self.assertFalse(self.packets)
        self.poll()
        self.assertEqual(self.sender.sent, 1)
        self.poll(False)
        self.poll()
        self.assertEqual(self.sender.sent, 2)

    def test_failure_telemetry_and_recovery(self):
        self.success = False
        self.interface.heartbeat(b'TEST')
        self.poll()
        self.assertEqual((self.sender.sent, self.sender.failures), (0, 1))
        self.assertEqual(self.sender.payload()[1], 2)
        self.success = True
        self.now = 2500
        self.poll()
        self.assertEqual((self.sender.sent, self.sender.failures), (1, 1))
        self.assertEqual(len(self.sender.payload()), 14)
        self.assertEqual(self.sender.payload()[1], 1)

    def test_field_can_parse_network_envelope_and_only_keepalive_is_sent(self):
        for tick in range(0, 10001, 500):
            self.now = tick
            self.interface.heartbeat(b'TEST')
            self.poll()
        self.assertEqual(len(self.packets), 5)
        for packet in self.packets:
            self.assertEqual(packet[:2], b'\x04\xd2')
            parser = nfcp.NFCPStreamParser()
            decoded = None
            for value in packet[2:]:
                decoded = parser.feed(value) or decoded
            self.assertEqual(decoded['cmd'], nfcp.CMD_KEEPALIVE)
            self.assertEqual(len(decoded['data']), 4)


if __name__ == '__main__':
    unittest.main()
