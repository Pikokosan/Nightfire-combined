"""Host tests for manual-fire cue scrolling and Trellis mapping."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class FakeTime:
    def __init__(self):
        self.now = 1000

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(new, old):
        return new - old


class FakeTrellis:
    def __init__(self):
        self.leds = set()
        self.pressed = set()

    def clear(self):
        self.leds.clear()

    def set_led(self, index):
        self.leds.add(index)

    def write_display(self):
        pass

    def read_switches(self):
        return bool(self.pressed)

    def just_pressed(self, index):
        return index in self.pressed


class ManualFireViewportTests(unittest.TestCase):
    def setUp(self):
        source = Path(__file__).resolve().parents[2] / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        gui = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NFCPGui"
        )
        names = {
            "sync_manual_fire_viewport",
            "update_trellis_continuity_leds",
            "fire_manual_cue",
            "poll_manual_fire_trellis",
        }
        gui.body = [
            node for node in gui.body
            if isinstance(node, ast.FunctionDef) and node.name in names
        ]
        self.time = FakeTime()
        namespace = {
            "time": self.time,
            "MANUAL_FIRE_DEBOUNCE_MS": 180,
            "TRELLIS_MANUAL_FIRE_DEBOUNCE_MS": 0,
            "TRELLIS_POLL_MS": 4,
        }
        exec(compile(ast.Module(body=[gui], type_ignores=[]), str(source), "exec"), namespace)

        self.Gui = namespace["NFCPGui"]
        self.app = self.Gui()
        self.app.current_view = "manual_fire"
        self.app.manual_grid_cue_count = 24
        self.app.manual_cue_cols = 4
        self.app.manual_cue_row_pitch = 62
        self.app.manual_cue_base = 0
        self.app.manual_fire_last_poll_ms = 0
        self.app.manual_fire_last_fire_ms = 0
        self.app.manual_fire_last_by_cue = {}
        self.app.manual_grid_box = SimpleNamespace(get_scroll_y=lambda: 124)
        self.app.trellis = FakeTrellis()
        self.app.get_manual_fire_module = lambda: {"cue_count": 24}
        self.app.get_module_cue_count = lambda module: module["cue_count"]
        self.app.get_module_continuity_list = lambda module: [i >= 8 for i in range(24)]
        self.app.update_manual_fire_last_indicator = Mock()
        self.app.clear_trellis_leds = Mock()
        self.app.fire_manual_cue = Mock()
        self.app.set_status = Mock()

    def test_bottom_view_maps_trellis_to_cues_9_through_24(self):
        self.app.sync_manual_fire_viewport()

        self.assertEqual(self.app.manual_cue_base, 8)
        self.assertEqual(self.app.trellis.leds, set(range(16)))

        self.app.trellis.pressed = {0}
        self.app.poll_manual_fire_trellis()
        self.app.fire_manual_cue.assert_called_once_with(8, 0)

    def test_partial_scroll_snaps_mapping_to_nearest_row(self):
        self.app.manual_grid_box = SimpleNamespace(get_scroll_y=lambda: 35)

        self.app.sync_manual_fire_viewport()

        self.assertEqual(self.app.manual_cue_base, 4)

    def test_different_trellis_keys_are_not_blocked_by_shared_debounce(self):
        self.app.manual_grid_box = SimpleNamespace(get_scroll_y=lambda: 0)

        self.app.trellis.pressed = {0}
        self.app.poll_manual_fire_trellis()
        self.time.now += 10
        self.app.trellis.pressed = {1}
        self.app.poll_manual_fire_trellis()

        self.assertEqual(
            self.app.fire_manual_cue.call_args_list,
            [
                unittest.mock.call(0, 0),
                unittest.mock.call(1, 0),
            ],
        )

    def test_different_cues_can_fire_inside_the_old_global_lockout(self):
        app = self.Gui()
        module = {"cue_count": 24, "unique_id": "0x00000001"}
        app.manual_fire_last_fire_ms = 0
        app.manual_fire_last_by_cue = {}
        app.manual_last_fire_uid = None
        app.manual_last_fire_key = None
        app.get_manual_fire_module = lambda: module
        app.get_module_cue_count = lambda selected: selected["cue_count"]
        app.get_module_saved_continuity = lambda selected: [False] * 24
        app.fire_cue = Mock()
        app.update_manual_fire_last_indicator = Mock()

        app.fire_manual_cue(0, 60)
        self.time.now += 10
        app.fire_manual_cue(1, 60)

        self.assertEqual(
            app.fire_cue.call_args_list,
            [unittest.mock.call(0), unittest.mock.call(1)],
        )

    def test_same_trellis_cue_can_fire_again_after_release(self):
        app = self.Gui()
        module = {"cue_count": 24, "unique_id": "0x00000001"}
        app.manual_fire_last_fire_ms = 0
        app.manual_fire_last_by_cue = {}
        app.manual_last_fire_uid = None
        app.manual_last_fire_key = None
        app.get_manual_fire_module = lambda: module
        app.get_module_cue_count = lambda selected: selected["cue_count"]
        app.get_module_saved_continuity = lambda selected: [False] * 24
        app.fire_cue = Mock()
        app.update_manual_fire_last_indicator = Mock()

        app.fire_manual_cue(0, 0)
        self.time.now += 10
        app.fire_manual_cue(0, 0)

        self.assertEqual(
            app.fire_cue.call_args_list,
            [unittest.mock.call(0), unittest.mock.call(0)],
        )


if __name__ == "__main__":
    unittest.main()
