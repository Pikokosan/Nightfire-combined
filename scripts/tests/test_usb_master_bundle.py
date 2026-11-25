import ast
import os
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

    def test_the_bridge_accepts_every_file_the_app_may_send(self):
        import re
        root = Path(__file__).resolve().parents[2]
        # The app is its own repo (nightfire-app), usually next to this one.
        app = Path(os.environ.get('NIGHTFIRE_APP') or root.parents[1] / 'nightfire-app')
        codec = app / 'lib/protocol/bridge_codec.dart'
        if not codec.exists():
            self.skipTest('nightfire-app not found at %s (set NIGHTFIRE_APP)' % app)
        dart = codec.read_text(encoding='utf-8')
        listed = re.search(r'const bridgeFirmwareFiles = \[(.*?)\];', dart, re.S).group(1)
        app_files = re.findall(r"'([^']+)'", listed)
        sys.path.insert(0, str(root / 'main'))
        from usb_master_maintenance import UPDATABLE_FILES
        self.assertIn('nfcp_frame.py', app_files)
        for name in app_files:
            self.assertIn(name, UPDATABLE_FILES)

    def test_no_top_level_name_is_defined_in_two_sources(self):
        # The bundle is one namespace: a second definition would silently
        # replace the first (bridge_radio.CMD_PING and NFCP's ping differ).
        from build_usb_master_bundle import SOURCES
        defined = {}
        for filename in SOURCES:
            source = Path(__file__).resolve().parents[2] / 'main' / filename
            for node in ast.parse(source.read_text(encoding='utf-8')).body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                    names = [node.name]
                elif isinstance(node, ast.Assign):
                    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                else:
                    continue
                for name in names:
                    self.assertNotIn(name, defined, '%s in %s and %s' % (name, defined.get(name), filename))
                    defined[name] = filename


if __name__ == '__main__':
    unittest.main()
