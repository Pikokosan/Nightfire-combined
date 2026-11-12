"""Pairing a module with a bridge: the module side (main/module_link.py)
and the bridge's offer window (main/usb_master_control.py BridgePairing).

Run: py -3 scripts/tests/test_module_pairing.py
"""
import sys
import time
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "main"))

_clock = {"ms": 1000}
_TIME_NAMES = ("ticks_ms", "ticks_us", "ticks_add", "ticks_diff", "sleep_ms")
_saved_time = {}


class FakeRadio(types.ModuleType):
    def __init__(self):
        super().__init__("nrf_radio")
        self.inits = []
        self.sent = []

    def init(self, channel=None, tx_power=None, address=None):
        self.inits.append(channel)

    def send(self, payload, **_):
        self.sent.append(bytes(payload))
        return True

    def device_id32(self):
        return 0x52840001

    def last_rssi(self):
        return -50


radio = FakeRadio()


def setUpModule():
    for name in _TIME_NAMES:
        _saved_time[name] = getattr(time, name, None)
    time.ticks_ms = lambda: _clock["ms"]
    time.ticks_us = lambda: _clock["ms"] * 1000
    time.ticks_add = lambda a, b: a + b
    time.ticks_diff = lambda a, b: a - b
    time.sleep_ms = lambda ms: None
    sys.modules.setdefault("machine", types.ModuleType("machine"))
    sys.modules.setdefault("module_ota", types.ModuleType("module_ota"))
    sys.modules["nrf_radio"] = radio
    global module_link, mesh, nfcp, control
    import mesh
    import nfcp
    import module_link
    import usb_master_control as control


def tearDownModule():
    for name, value in _saved_time.items():
        if value is None:
            delattr(time, name)
        else:
            setattr(time, name, value)


class Node:
    def __init__(self):
        self.network, self.group, self.uid, self.channel = 0x04D2, 3, 0x11223344, 90
        self.is_armed = False
        self.saved = []

    def ids(self):
        return self.network, self.group, self.uid, self.channel

    def armed(self):
        return self.is_armed

    def apply_setting(self, name, value):
        self.saved.append((name, value))
        setattr(self, name, value)
        return True


BRIDGE = 0xB29F9F33


class ModulePairingTests(unittest.TestCase):
    def setUp(self):
        _clock["ms"] = 1000
        radio.inits.clear()
        radio.sent.clear()
        self.node = Node()
        self.link = module_link.ModuleLink(self.node, 0x4E465752, 8, cca_enabled=False)
        self.offers = control.BridgePairing(lambda: _clock["ms"], lambda a, b: a - b)

    def offer(self, network=0x0BEE, channel=76):
        self.offers.start(5)
        frame = self.offers.poll(network, channel)
        payload = mesh.mesh_build(control.PAIR_NETWORK_ID, BRIDGE, 7, frame)
        self.link.receive(payload, self.link.handle_common)

    def replies(self):
        """CMD_PAIR_RSP data sent by the module."""
        found = []
        for payload in radio.sent:
            frame = payload[mesh.MESH_HEADER_LEN:]
            pkt = module_link.link_decode_nfcp(frame, 0)
            if pkt and pkt["cmd"] == nfcp.CMD_PAIR_RSP:
                found.append(bytes(pkt["data"]))
        return found

    def test_shared_constants_match(self):
        self.assertEqual(nfcp.PAIR_NETWORK_ID, control.PAIR_NETWORK_ID)
        self.assertEqual(nfcp.PAIR_RADIO_CHANNEL, control.PAIR_RADIO_CHANNEL)

    def test_pairing_takes_the_bridge_network_and_channel(self):
        self.assertTrue(self.link.start_pairing())
        self.assertEqual(radio.inits[-1], nfcp.PAIR_RADIO_CHANNEL)
        self.assertEqual(self.link.ids()[0], nfcp.PAIR_NETWORK_ID)
        self.offer()
        self.assertEqual(self.node.saved, [("network", 0x0BEE), ("channel", 76)])
        self.assertEqual(self.link.paired, (0x0BEE, 76))
        self.assertEqual(self.link.ids(), (0x0BEE, 3, 0x11223344, 76))
        self.assertEqual(radio.inits[-1], 76)
        nonce = self.offers.nonce.to_bytes(2, "big")
        # Sent with the usual repeats, all on the pairing network before the move.
        self.assertEqual(set(self.replies()), {b"\x0b\xee\x4c" + nonce + b"\x00\x03"})
        self.assertTrue(all(int.from_bytes(p[0:2], "big") == nfcp.PAIR_NETWORK_ID
                            for p in radio.sent))

    def test_offer_ignored_outside_pairing_mode_or_when_armed(self):
        self.offer()
        self.assertEqual(self.node.saved, [])
        self.node.is_armed = True
        self.assertFalse(self.link.start_pairing())
        self.assertEqual(self.link.ids()[0], 0x04D2)

    def test_window_times_out_and_arming_ends_it(self):
        self.link.start_pairing()
        _clock["ms"] += module_link.PAIR_WINDOW_MS + 1
        self.link.service()
        self.assertEqual(self.link.ids(), (0x04D2, 3, 0x11223344, 90))
        self.assertEqual(radio.inits[-1], 90)
        self.link.start_pairing()
        self.node.is_armed = True
        self.link.service()
        self.assertEqual(self.link.pair_until, None)


class BridgePairingTests(unittest.TestCase):
    def setUp(self):
        _clock["ms"] = 1000
        self.pairing = control.BridgePairing(lambda: _clock["ms"], lambda a, b: a - b)

    def test_offers_repeat_until_the_window_ends(self):
        self.assertFalse(self.pairing.start(0))
        self.assertFalse(self.pairing.start(31))
        self.assertTrue(self.pairing.start(2))
        self.assertEqual(self.pairing.seconds_left(), 2)
        first = self.pairing.poll(0x04D2, 90)
        self.assertEqual(first, control.broadcast(
            0x34, b"\x04\xd2\x5a" + self.pairing.nonce.to_bytes(2, "big")))
        self.assertIsNone(self.pairing.poll(0x04D2, 90))
        _clock["ms"] += control.PAIR_OFFER_GAP_MS
        self.assertIsNotNone(self.pairing.poll(0x04D2, 90))
        _clock["ms"] += 2000
        self.assertIsNone(self.pairing.poll(0x04D2, 90))
        self.assertFalse(self.pairing.active())

    def test_offer_decodes_as_a_module_broadcast(self):
        self.pairing.start(1)
        frame = self.pairing.poll(0x04D2, 90)
        pkt = module_link.link_decode_nfcp(frame, 0)
        self.assertEqual(pkt["frame_type"], nfcp.SOF_BROADCAST)
        self.assertEqual(pkt["cmd"], nfcp.CMD_PAIR)


if __name__ == "__main__":
    unittest.main()
