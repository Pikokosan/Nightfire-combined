"""The bridges' NFCP codec (main/nfcp_frame.py) matches main/nfcp.py.

Run: py -3 scripts/tests/test_nfcp_frame.py
"""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "main"))
import nfcp
import nfcp_frame


class ConstantTests(unittest.TestCase):
    def test_every_command_matches_nfcp(self):
        names = [name for name in dir(nfcp_frame) if name.startswith("NFCP_CMD_")]
        self.assertGreater(len(names), 20)
        for name in names:
            self.assertEqual(getattr(nfcp_frame, name), getattr(nfcp, name[len("NFCP_"):]), name)

    def test_frame_kinds_and_shared_values_match_nfcp(self):
        self.assertEqual(nfcp_frame.NFCP_ESC, nfcp.ESC)
        self.assertEqual(nfcp_frame.NFCP_KIND_BROADCAST, nfcp.SOF2_BROADCAST)
        self.assertEqual(nfcp_frame.NFCP_KIND_UNIQUE, nfcp.SOF2_UNIQUE)
        self.assertEqual(nfcp_frame.NFCP_KIND_RESPONSE, nfcp.SOF2_RESPONSE)
        self.assertEqual(nfcp_frame.NFCP_PAIR_NETWORK_ID, nfcp.PAIR_NETWORK_ID)
        self.assertEqual(nfcp_frame.NFCP_PAIR_RADIO_CHANNEL, nfcp.PAIR_RADIO_CHANNEL)
        self.assertEqual(nfcp_frame.NFCP_SCRIPT_ENTRY_BYTES, nfcp.SCRIPT_ENTRY_BYTES)
        self.assertEqual(nfcp_frame.NFCP_ACK_OK, nfcp.ACK_OK)

    def test_every_top_level_name_is_prefixed(self):
        # The USB master bundle shares one namespace with bridge_radio.py.
        for name in vars(nfcp_frame):
            if not name.startswith("__") and name != "bridge_radio":
                self.assertTrue(name.startswith(("NFCP_", "nfcp_")), name)

    def test_valid_channels(self):
        valid = [channel for channel in range(256) if nfcp_frame.nfcp_valid_channel(channel)]
        self.assertEqual(valid, [0] + list(range(74, 101)))


class CodecTests(unittest.TestCase):
    SOF = {
        nfcp_frame.NFCP_KIND_BROADCAST: nfcp.SOF_BROADCAST,
        nfcp_frame.NFCP_KIND_UNIQUE: nfcp.SOF_UNIQUE,
        nfcp_frame.NFCP_KIND_RESPONSE: nfcp.SOF_RESPONSE,
    }

    def setUp(self):
        self.protocol = nfcp.NFCP(1)
        self.random = random.Random(7)

    def payloads(self):
        yield b"\x01\x40"
        yield b"\xff" * 12                        # every byte escaped
        yield bytes(range(256))[:200]
        for unused in range(200):
            yield bytes(self.random.getrandbits(8) for unused in range(self.random.randint(2, 200)))

    def test_encode_matches_nfcp_and_decodes_back(self):
        for kind, sof in self.SOF.items():
            for payload in self.payloads():
                frame = nfcp_frame.nfcp_encode(kind, payload)
                self.assertEqual(frame, self.protocol._frame(sof, payload))
                if len(frame) <= nfcp_frame.NFCP_MAX_TX_FRAME:
                    self.assertEqual(nfcp_frame.nfcp_decode(frame, (kind,)), (kind, payload))

    def test_broadcast_matches_nfcp(self):
        for command in (nfcp.CMD_DISARM, nfcp.CMD_KEEPALIVE, nfcp.CMD_PAIR, nfcp.CMD_SCRIPT_STOP):
            for data in (b"", b"\x00\x00", b"\xff\xfe\xff\x01"):
                self.assertEqual(nfcp_frame.nfcp_broadcast(command, data),
                                 self.protocol.build_broadcast(command, data))

    def test_decode_rejects_bad_frames(self):
        good = self.protocol.build_ping(0x12FF34FF, b"\xff")
        decode = lambda frame: nfcp_frame.nfcp_decode(frame, (nfcp_frame.NFCP_KIND_UNIQUE,))
        self.assertIsNotNone(decode(good))
        self.assertIsNone(nfcp_frame.nfcp_decode(good, (nfcp_frame.NFCP_KIND_BROADCAST,)))
        self.assertIsNone(decode(good[:-1]))                          # truncated
        self.assertIsNone(decode(good[:-1] + bytes([good[-1] ^ 1])))  # CRC
        self.assertIsNone(decode(b"\x00" + good[1:]))                 # no ESC
        index = good.index(b"\xff\xfe", 2)
        self.assertIsNone(decode(good[:index + 1] + b"\x00" + good[index + 2:]))  # bad escape
        self.assertIsNone(decode(good[:-1] + b"\xff"))                # dangling ESC
        self.assertIsNone(decode(b"\xff\xfb\x00"))                    # too short
        too_long = nfcp_frame.nfcp_encode(nfcp_frame.NFCP_KIND_UNIQUE, b"\x01" * 214)
        self.assertGreater(len(too_long), nfcp_frame.NFCP_MAX_TX_FRAME)
        self.assertIsNone(decode(too_long))


if __name__ == "__main__":
    unittest.main()
