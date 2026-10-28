"""Host regression tests for delayed continuity replies; no board or LVGL required."""
import ast
import contextlib
import copy
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class LateContinuityTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[1] / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        gui = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NFCPGui")
        names = {"poll_nfcp_responses", "load_modules_from_db", "format_module_continuity_summary",
                 "module_has_continuity_data", "module_matches_selected", "get_module_continuity_list",
                 "get_module_saved_continuity"}
        gui.body = [n for n in gui.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.db = {"devices": [{"unique_id": "0x00000001", "group_id": "0x0001",
                                "cue_count": 2, "module_online": False}]}
        self.packets = []
        self.protocol = SimpleNamespace(
            CMD_ACK=1, CMD_NACK=2, CMD_DISCOVER_RSP=3, CMD_CONTINUITY_RSP=4,
            poll_responses=lambda: self.packets,
            load_device_db=lambda: copy.deepcopy(self.db),
            index_db_by_unique=lambda db: {d["unique_id"]: d for d in db["devices"]},
            save_device_db=self.save_db,
            parse_continuity_rsp=lambda data, count: [bool(v) for v in data[:count]],
        )
        namespace = {"nfcp": self.protocol, "time": SimpleNamespace(time=lambda: 100),
                     "module_mark_online": lambda dev: dev.update(module_online=True),
                     "module_apply_radio_link": lambda dev, uid: None}
        exec(compile(ast.Module(body=[gui], type_ignores=[]), str(source), "exec"), namespace)
        self.app = namespace["NFCPGui"]()
        self.app.current_view = "modules"
        self.app.modules_page = 2
        self.app.selected_module = None
        self.app.continuity = [False, False]
        self.app.get_module_cue_count = lambda dev: dev["cue_count"]
        self.app.group_to_int_value = lambda group: int(group, 16)
        self.app.set_status = Mock()
        self.app.refresh_selected_module_detail_labels = Mock()
        self.app.set_continuity = lambda ok: setattr(self.app, "continuity", ok)
        self.app.show_modules_menu = Mock(side_effect=self.render_menu)
        for name in ("update_script_module_mismatch", "update_open_script_cues",
                     "update_extra_connected_cues", "update_arm_button"):
            setattr(self.app, name, Mock())
        self.rendered = []

    def save_db(self, db):
        self.db = copy.deepcopy(db)

    def render_menu(self, restore_selected_page=True):
        if restore_selected_page:
            self.app.modules_page = 0
        self.rendered = [self.app.format_module_continuity_summary(d) for d in self.app.modules]

    def reply(self, uid=1, data=b"\x01\x00"):
        self.packets.append({"cmd": 4, "src_unique": uid, "data": data})

    def poll(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.app.poll_nfcp_responses()

    def test_late_reply_updates_visible_button_and_preserves_page(self):
        self.reply()
        self.poll()
        self.assertEqual(self.rendered, ["1/2"])
        self.assertEqual(self.app.modules_page, 2)
        self.assertTrue(self.db["devices"][0]["module_online"])
        self.app.show_modules_menu.assert_called_once_with(restore_selected_page=False)

    def test_selected_module_uses_late_values_instead_of_stale_cache(self):
        self.app.selected_module = copy.deepcopy(self.db["devices"][0])
        self.reply(data=b"\x01\x01")
        self.poll()
        self.assertEqual(self.rendered, ["2/2"])
        self.assertEqual(self.app.continuity, [True, True])

    def test_other_screen_keeps_its_view_and_saves_late_reply(self):
        self.app.current_view = "module_continuity"
        self.reply()
        self.poll()
        self.app.show_modules_menu.assert_not_called()
        self.assertEqual(self.db["devices"][0]["continuity"]["cues"][0], {"cue": 1, "ok": True})

    def test_unrelated_packets_do_not_rebuild_menu(self):
        self.reply(uid=99)
        self.packets.append({"cmd": 1, "src_unique": 1, "data": b""})
        self.poll()
        self.app.show_modules_menu.assert_not_called()
        self.assertNotIn("continuity", self.db["devices"][0])

    def test_reply_batch_renders_once_with_latest_values(self):
        self.reply(data=b"\x00\x00")
        self.reply(data=b"\x01\x01")
        self.poll()
        self.assertEqual(self.rendered, ["2/2"])
        self.app.show_modules_menu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
