import ast
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
from build_usb_master_bundle import bundle_source


class BundleTests(unittest.TestCase):
    def test_bundled_protocol_namespace_and_watchdog_match_sources(self):
        source = bundle_source(Path(__file__).resolve().parents[2] / 'main')
        tree = ast.parse(source)
        definitions = []
        for node in tree.body:
            if isinstance(node, ast.Import) and any(name.name == 'nrf_radio' for name in node.names):
                break  # Do not run hardware initialization or the main loop.
            definitions.append(node)
        namespace = {}
        exec(compile(ast.Module(body=definitions, type_ignores=[]), '<bundle>', 'exec'), namespace)
        bridge = namespace['bridge_radio']
        encoded = bridge.build_frame(7, 1)
        parser = bridge.BridgeFrameParser()
        result = None
        for value in encoded:
            result = parser.feed(value)
        self.assertEqual((result.seq, result.cmd, result.payload), (7, 1, b''))
        self.assertEqual(bridge.crc16_ccitt(b'123456789'), 0x29b1)
        frames = []
        master = namespace['MasterControl'](lambda packet: frames.append(packet) or True,
                                            lambda: 0, lambda a, b: a - b)
        master.may_be_armed = True
        master.poll(False, True)
        self.assertEqual(len(frames), 2)
        self.assertTrue(master.needs_rearm)
        self.assertEqual(namespace['MasterKeepalive'](1234, None, None, None).network, 1234)
        self.assertIn('BridgeMaintenance', namespace)


if __name__ == '__main__':
    unittest.main()
