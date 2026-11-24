"""32 kHz RC calibration (nrf_module_common.LfrcCalibrator): on a board whose
32 kHz crystal doesn't run, ticks_ms() follows the RC oscillator (COM16 ran
0.77% slow), so the module link calibrates it against the 32 MHz crystal
every 4 s. Fake CLOCK registers here.

Run: py -3 scripts/tests/test_lfrc_calibration.py
"""
import contextlib
import io
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
clock = {"ms": 1000}
fake_time = types.ModuleType("time")
fake_time.ticks_ms = lambda: clock["ms"]
fake_time.ticks_diff = lambda a, b: a - b
fake_time.sleep_ms = lambda ms: None
sys.modules.setdefault("machine", types.ModuleType("machine"))
sys.modules.setdefault("nrf_radio", types.ModuleType("nrf_radio"))
saved_time = sys.modules.get("time")
sys.modules["time"] = fake_time
sys.path.insert(0, str(ROOT / "main"))
try:
    import nrf_module_common as common
finally:
    sys.modules["time"] = saved_time

LF_RC, LF_XTAL = 0x10000, 0x10001        # LFCLKSTAT: running, source RC / crystal
HF_XTAL, HF_RC = 0x10001, 0x10000


class Mem(dict):
    """CLOCK registers; records task writes."""

    def __init__(self, lf, hf=HF_XTAL):
        super().__init__({common.LFRC_LFCLKSTAT: lf, common.LFRC_HFCLKSTAT: hf})
        self.cal_tasks = 0

    def __setitem__(self, key, value):
        if key == common.LFRC_TASKS_CAL and value == 1:
            self.cal_tasks += 1
        dict.__setitem__(self, key, value)


def make(mem):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        cal = common.LfrcCalibrator(mem)
    return cal, out.getvalue()


class LfrcTests(unittest.TestCase):
    def test_crystal_board_is_left_alone(self):
        mem = Mem(LF_XTAL)
        cal, out = make(mem)
        self.assertFalse(cal.active)
        self.assertEqual(out, "")
        clock["ms"] += 60000
        cal.service()
        self.assertEqual(mem.cal_tasks, 0)

    def test_rc_board_calibrates_at_once_and_every_4_s(self):
        mem = Mem(LF_RC)
        cal, out = make(mem)
        self.assertTrue(cal.active)
        self.assertIn("LFCLK on RC", out)
        self.assertEqual(mem.cal_tasks, 1)
        self.assertEqual(mem[common.LFRC_EVENTS_DONE], 0)     # cleared before the task
        for unused in range(399):                              # 3.99 s
            clock["ms"] += 10
            cal.service()
        self.assertEqual(mem.cal_tasks, 1)
        clock["ms"] += 10
        cal.service()
        self.assertEqual(mem.cal_tasks, 2)
        clock["ms"] += 40000
        cal.service()
        self.assertEqual(mem.cal_tasks, 3)                    # one task per service, not a burst

    def test_never_calibrates_against_the_rc_32_mhz_clock(self):
        mem = Mem(LF_RC, hf=HF_RC)
        cal, out = make(mem)
        self.assertTrue(cal.active)
        clock["ms"] += 5000
        cal.service()
        self.assertEqual(mem.cal_tasks, 0)
        mem[common.LFRC_HFCLKSTAT] = HF_XTAL                   # the crystal comes up
        clock["ms"] += 5000
        cal.service()
        self.assertEqual(mem.cal_tasks, 1)

    def test_no_registers_means_nothing_to_do(self):
        class NoMem:
            def __getitem__(self, key):
                raise OSError("no such register")
        cal, out = make(NoMem())
        self.assertFalse(cal.active)
        cal.service()

    def test_module_link_runs_it_every_loop(self):
        text = (ROOT / "main" / "module_link.py").read_text(encoding="utf-8")
        self.assertIn("self.lfrc = nrf_module_common.LfrcCalibrator()", text)
        service = text.split("def service(self):", 1)[1].split("def ", 1)[0]
        self.assertIn("self.lfrc.service()", service)
        bundle = (ROOT / "scripts" / "build" / "build_field_mpy_bundle.py").read_text(encoding="utf-8")
        self.assertIn("nrf_module_common.LfrcCalibrator = LfrcCalibrator", bundle)


if __name__ == "__main__":
    unittest.main()
