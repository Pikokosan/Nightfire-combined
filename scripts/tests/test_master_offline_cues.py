"""Host regression test for an offline module's continuity display."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


class FakeLabel:
    def __init__(self):
        self.text = ""

    def set_text(self, text):
        self.text = text

    def center(self):
        pass


class FakeButton:
    def __init__(self):
        self.color = None

    def set_style_bg_color(self, color, selector):
        self.color = color


class OfflineCueDisplayTests(unittest.TestCase):
    def test_all_cues_are_gray_when_selected_module_is_offline(self):
        source = Path(__file__).resolve().parents[2] / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        gui = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "NFCPGui"
        )
        gui.body = [
            node for node in gui.body
            if isinstance(node, ast.FunctionDef) and node.name in {
                "module_is_lost",
                "refresh_cues",
            }
        ]
        namespace = {
            "lv": SimpleNamespace(color_hex=lambda value: value),
            "RESISTANCE_SHORT_MAX_DECI_OHM": 0,
        }
        exec(compile(ast.Module(body=[gui], type_ignores=[]), str(source), "exec"), namespace)

        app = namespace["NFCPGui"]()
        app.selected_module = {"module_online": False, "cue_count": 3}
        app.show_resistance_values = False
        app.continuity = [True, True, True]
        app.cue_labels = [FakeLabel() for _ in range(3)]
        app.cue_buttons = [FakeButton() for _ in range(3)]
        app.summary = FakeLabel()
        app.get_selected_module_cue_count = lambda: 3
        app.selected_cue_is_in_script = lambda cue: True
        app.set_status = lambda text: None

        app.refresh_cues()

        self.assertEqual(
            [label.text for label in app.cue_labels],
            ["Cue 01: NO COMM", "Cue 02: NO COMM", "Cue 03: NO COMM"],
        )
        self.assertEqual([button.color for button in app.cue_buttons], [0x4A4A4A] * 3)
        self.assertEqual(app.summary.text, "Continuity: -- / 3")


if __name__ == "__main__":
    unittest.main()
