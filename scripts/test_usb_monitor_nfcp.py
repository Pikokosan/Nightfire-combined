import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "main"))
import nfcp
from usb_monitor_nfcp import decode_query, radio_reply


class QueryGateTests(unittest.TestCase):
    def setUp(self):
        self.protocol = nfcp.NFCP(1)
        self.uid = 0x12FF5678

    def test_existing_query_frames_are_accepted(self):
        for frame in (
            self.protocol.build_discover_broadcast(0xFF55),
            self.protocol.build_discover_broadcast(),
            self.protocol.build_info_request(self.uid),
            self.protocol.build_resistance_request(self.uid),
            self.protocol.build_continuity_request(self.uid),
        ):
            self.assertIsNotNone(decode_query(frame))

    def test_actuation_configuration_and_keepalive_are_rejected(self):
        for frame in (
            self.protocol.build_arm_broadcast(),
            self.protocol.build_arm_unique(self.uid),
            self.protocol.build_unique_fire(self.uid, 1),
            self.protocol.build_group_fire(1, 1),
            self.protocol.build_script_start_broadcast(1, 2000),
            self.protocol.build_keepalive_broadcast(),
            self.protocol.build_set_group(self.uid, 2),
            self.protocol.build_resistance_request(self.uid, True),
        ):
            self.assertIsNone(decode_query(frame))

    def test_malformed_crc_escaping_and_concatenation_are_rejected(self):
        frame = self.protocol.build_info_request(self.uid)
        corrupt = bytearray(frame)
        corrupt[-1] ^= 1
        for value in (corrupt, frame[:-1], frame + frame, frame + b"\xff", b"\xff\xfb\xff\x10"):
            self.assertIsNone(decode_query(value))

    def test_unique_command_allowlist_is_exhaustive(self):
        for command in range(256):
            frame = self.protocol.build_unique_command(self.uid, command)
            self.assertEqual(decode_query(frame) is not None, command in (0x22, 0x24, 0x52))

    def test_group_setting_requires_active_host_and_exact_unique_payload(self):
        frame = self.protocol.build_set_group(self.uid, 65535)
        self.assertIsNone(decode_query(frame))
        self.assertIsNotNone(decode_query(frame, allow_settings=True))
        for data in (b'', b'\x01', b'\x00\x01\x02'):
            self.assertIsNone(decode_query(self.protocol.build_unique_command(self.uid, nfcp.CMD_SET_GROUP, data), True))
        for command in (nfcp.CMD_ARM, nfcp.CMD_FIRE, nfcp.CMD_SET_NETWORK):
            self.assertIsNone(decode_query(self.protocol.build_unique_command(self.uid, command, b'\x00\x01'), True))

    def test_network_filter_and_both_radio_envelopes(self):
        reply = self.protocol.build_response(self.uid, nfcp.CMD_INFO_RSP, b"\x00")
        self.assertEqual(radio_reply(b"\x04\xd2" + reply, 1234), reply)
        self.assertEqual(radio_reply(b"\x04\xd2\x03" + reply, 1234), reply)
        self.assertIsNone(radio_reply(b"\x04\xd3\x03" + reply, 1234))
        self.assertIsNone(radio_reply(b"\x04\xd2\x03" + self.protocol.build_arm_broadcast(), 1234))


if __name__ == "__main__":
    unittest.main()
