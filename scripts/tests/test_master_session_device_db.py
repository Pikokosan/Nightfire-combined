"""Host tests for the command module's session-only discovered-device data."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class SessionDeviceDatabaseTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        nodes = []
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                if "_device_db" in names:
                    nodes.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name in {
                "load_device_db",
                "save_device_db",
                "clear_device_db",
            }:
                nodes.append(node)

        namespace = {
            "DEVICE_DB_PATH": "nfcp_devices.json",
            "time": SimpleNamespace(time=lambda: 123),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), namespace)
        self.namespace = namespace

    def test_saved_modules_are_available_during_current_session(self):
        db = {"devices": [{"unique_id": "0x00000001"}]}
        self.namespace["save_device_db"](db)

        loaded = self.namespace["load_device_db"]()
        self.assertEqual(loaded["device_count"], 1)
        self.assertEqual(loaded["generated_epoch"], 123)

    def test_clear_replaces_session_database(self):
        self.namespace["save_device_db"]({"devices": [{"unique_id": "x"}]})
        self.namespace["clear_device_db"]()

        self.assertEqual(self.namespace["load_device_db"]()["devices"], [])


if __name__ == "__main__":
    unittest.main()
