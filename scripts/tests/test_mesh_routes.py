"""Route choice of the bridge's reliable delivery (main/mesh.py MeshBridge).

Run: py -3 scripts/tests/test_mesh_routes.py
"""
import sys
import time
import unittest
from pathlib import Path

# MicroPython's tick functions on a fake clock.
_clock = {"ms": 1000}
time.ticks_ms = lambda: _clock["ms"]
time.ticks_us = lambda: _clock["ms"] * 1000
time.ticks_add = lambda a, b: a + b
time.ticks_diff = lambda a, b: a - b

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "main"))
import mesh  # noqa: E402

NET = 0x04D2
BRIDGE, RELAY, MODULE = 0xB29F9F33, 0xD96E30DD, 0x62A75130
FRAME = bytes([0xFF, 0xA5, 3, 1, 2, 3])


class RouteTests(unittest.TestCase):
    def setUp(self):
        _clock["ms"] = 1000
        self.sent = []
        self.bridge = mesh.MeshBridge(lambda payload: self.sent.append(bytes(payload)) or True, BRIDGE)

    def advance(self, ms):
        _clock["ms"] += ms

    def hear(self, hops, rssi, msg_id=1, relay=None):
        """A frame from MODULE heard with this many relay hops."""
        payload = mesh.mesh_build(NET, MODULE, msg_id, FRAME, hops=hops, relay=relay)
        self.bridge.receive(payload, NET, rssi)

    def route(self):
        return self.bridge.route_for(MODULE, time.ticks_ms())

    def deliver(self):
        """Start a delivery; returns it and the mode of the first attempt."""
        delivery = self.bridge.send_reliable(NET, MODULE, FRAME, 3000, tag=1)
        mode = (mesh.mesh_parse(self.sent[-1])[0] >> 4) & 3
        return delivery, mode

    def reply(self, delivery, hops, rssi, relay=None):
        self.hear(hops, rssi, msg_id=delivery["id"], relay=relay)
        return self.bridge.poll()

    def test_strong_direct_link_goes_direct(self):
        self.hear(0, -60)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_DIRECT, None))

    def test_weak_direct_link_with_no_relay_floods(self):
        self.hear(0, -84)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_FLOOD, None))

    def test_relay_with_weak_direct_is_preferred_and_sticks(self):
        self.hear(0, -84)
        self.hear(1, -55, relay=RELAY)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_VIA, RELAY))
        # A marginal direct copy that reads strong enough must not flip it back.
        self.hear(0, -78, msg_id=2)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_VIA, RELAY))
        # Even after the route ages past the freshness window.
        self.advance(mesh.MESH_ROUTE_FRESH_MS + 1000)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_VIA, RELAY))

    def test_a_via_delivery_reports_via_even_if_a_direct_copy_arrives_first(self):
        self.hear(0, -84)
        self.hear(1, -55, relay=RELAY)
        delivery, mode = self.deliver()
        self.assertEqual(mode, mesh.MESH_MODE_VIA)
        result = self.reply(delivery, 0, -83)       # the module's own transmission
        self.assertEqual(result[0][1][1], mesh.MESH_ROUTE_VIA)
        self.assertEqual(int.from_bytes(result[0][1][2:6], "big"), RELAY)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_VIA, RELAY))

    def test_a_failed_relay_is_dropped_and_the_flood_finds_the_way(self):
        self.hear(0, -84)
        self.hear(1, -55, relay=RELAY)
        delivery, mode = self.deliver()
        self.assertEqual(mode, mesh.MESH_MODE_VIA)
        self.advance(delivery["per"])
        self.bridge.poll()                          # attempt 1 timed out
        self.assertEqual((mesh.mesh_parse(self.sent[-1])[0] >> 4) & 3, mesh.MESH_MODE_FLOOD)
        self.assertNotEqual(self.route()[0], mesh.MESH_ROUTE_VIA)
        result = self.reply(delivery, 1, -50, relay=0x11112222)   # another relay answers
        self.assertEqual(result[0][1][0], 0)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_VIA, 0x11112222))

    def test_a_working_direct_route_sticks_while_it_works(self):
        self.hear(0, -70)
        delivery, mode = self.deliver()
        self.assertEqual(mode, mesh.MESH_MODE_DIRECT)
        self.reply(delivery, 0, -70)
        # A relayed copy turning up later doesn't take over a working direct route.
        self.hear(1, -55, msg_id=9, relay=RELAY)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_DIRECT, None))

    def test_a_module_carried_close_leaves_the_relay(self):
        self.hear(0, -84)
        self.hear(1, -55, relay=RELAY)
        self.hear(0, -50, msg_id=3)
        self.assertEqual(self.route(), (mesh.MESH_ROUTE_DIRECT, None))


if __name__ == "__main__":
    unittest.main()
