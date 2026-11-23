"""Every disarm path of the field and dummy module firmware leaves the module
SAFE: field_main.disarm() and nrf_dummy_main.disarm().

The firmware runs on the fake hardware of scripts/sim/firmware_trace_harness.py.

Run: py -3 scripts/tests/test_module_disarm.py
"""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class StopLoop(Exception):
    pass


def load_harness():
    spec = importlib.util.spec_from_file_location(
        "firmware_trace_harness", ROOT / "scripts" / "sim" / "firmware_trace_harness.py")
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return harness


def load(harness, fw):
    """Import one firmware on the fake hardware, then put the real time and
    machine modules back for unittest (the firmware keeps the fakes)."""
    saved = {name: sys.modules.get(name) for name in ("time", "utime", "machine")}
    cwd = os.getcwd()
    work = tempfile.mkdtemp(prefix="fwdisarm-")
    os.chdir(work)
    try:
        harness.install_fakes(fw)
        # Service mode stops field_main from entering its main loop on import.
        open("field_service.flag", "w").close()
        with contextlib.redirect_stdout(io.StringIO()):
            return harness.load_firmware(fw, str(ROOT / "main"))
    finally:
        os.chdir(cwd)
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


HARNESS = load_harness()
FIELD = load(HARNESS, "field")
DUMMY = load(HARNESS, "dummy")


class LoopTime:
    """The firmware's fake time, except that the main loop's error-recovery
    sleep ends the loop."""

    def __getattr__(self, name):
        return getattr(HARNESS.fake_time, name)

    def sleep_ms(self, ms):
        raise StopLoop()


def run_one_failing_loop(fw):
    """Run fw.main() until a script error has gone through its except path."""
    def fail():
        raise RuntimeError("test loop error")

    patches = {"setup": lambda: True, "time": LoopTime()}
    saved = {name: getattr(fw, name) for name in patches}
    saved_run = fw.script.run
    try:
        for name, value in patches.items():
            setattr(fw, name, value)
        fw.script.run = fail
        with contextlib.redirect_stdout(io.StringIO()):
            with unittest.TestCase().assertRaises(StopLoop):
                fw.main()
    finally:
        for name, value in saved.items():
            setattr(fw, name, value)
        fw.script.run = saved_run


def broadcast(fw, cmd, data=b""):
    # Broadcast frames get no ACK/NACK, so nothing is queued for the radio.
    return {"cmd": cmd, "frame_type": fw.SOF_BROADCAST, "data": data}


class FieldDisarmTests(unittest.TestCase):
    fw = FIELD

    def setUp(self):
        fw = self.fw
        HARNESS.ADC.reading = 40000
        fw.STARTUP_FAULT = False
        fw.STARTUP_FAULT_REASON = ""
        fw.arm_check_error = ""
        with contextlib.redirect_stdout(io.StringIO()):
            fw.handle_arm(broadcast(fw, fw.CMD_ARM))
            self.assertTrue(fw.ARMED)
            self.assertEqual(fw.fire_mask(0b101), fw.ACK_OK)
        fw.script.running = True
        fw.pre_test.value(1)
        self.assertSafe(False)

    def tearDown(self):
        self.fw.STARTUP_FAULT = False
        self.fw.STARTUP_FAULT_REASON = ""
        HARNESS.ADC.reading = 40000

    def assertSafe(self, safe=True):
        fw = self.fw
        inactive = 1 - fw.ARM_CONTROL_ACTIVE_VALUE
        state = (
            fw.ARMED, fw.script.running, fw.fire_out_mask != 0, fw.pre_test.value(),
            fw.outputs.is_enabled(), fw.arm_control.value() != inactive,
            fw.safe_led.value(), fw.arm_led.value(),
        )
        if safe:
            self.assertEqual(state, (False, False, False, 0, False, False, 1, 0))
        else:
            self.assertEqual(state, (True, True, True, 1, True, True, 0, 1))

    def quiet(self, call, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            call(*args)
        return out.getvalue()

    def test_disarm_command(self):
        out = self.quiet(self.fw.handle, broadcast(self.fw, self.fw.CMD_DISARM))
        self.assertSafe()
        self.assertIn("DISARMED", out)

    def test_master_link_timeout(self):
        fw = self.fw
        fw.link.last_rx_ms = fw.time.ticks_ms()
        HARNESS._clock["ms"] += fw.MASTER_LINK_TIMEOUT_MS + 1
        out = self.quiet(fw.check_master_link_failsafe)
        self.assertSafe()
        self.assertIn("master link timeout", out)

    def test_startup_fault(self):
        self.quiet(self.fw.set_startup_fault, "test fault")
        self.assertSafe()
        self.assertTrue(self.fw.STARTUP_FAULT)

    def test_arm_with_startup_fault_is_rejected_safe(self):
        self.fw.STARTUP_FAULT = True
        self.quiet(self.fw.handle_arm, broadcast(self.fw, self.fw.CMD_ARM))
        self.assertSafe()

    def test_failed_arm_voltage_check_is_rejected_safe(self):
        HARNESS.ADC.reading = 0     # SHORT_CHECK reads 0 mV with ARM_CONTROL on
        self.quiet(self.fw.handle_arm, broadcast(self.fw, self.fw.CMD_ARM))
        self.assertSafe()
        self.assertTrue(self.fw.arm_check_error)

    def test_main_loop_error(self):
        run_one_failing_loop(self.fw)
        self.assertSafe()


class DummyDisarmTests(unittest.TestCase):
    fw = DUMMY

    def setUp(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.fw.handle(broadcast(self.fw, self.fw.CMD_ARM))
        self.fw.script.running = True
        self.assertTrue(self.fw.ARMED)

    def assertSafe(self):
        self.assertFalse(self.fw.ARMED)
        self.assertFalse(self.fw.script.running)

    def test_disarm_command(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.fw.handle(broadcast(self.fw, self.fw.CMD_DISARM))
        self.assertSafe()

    def test_master_link_timeout(self):
        fw = self.fw
        fw.link.last_rx_ms = fw.time.ticks_ms()
        HARNESS._clock["ms"] += fw.MASTER_LINK_TIMEOUT_MS + 1
        with contextlib.redirect_stdout(io.StringIO()):
            fw.check_master_link_failsafe()
        self.assertSafe()

    def test_main_loop_error(self):
        run_one_failing_loop(self.fw)
        self.assertSafe()


if __name__ == "__main__":
    unittest.main()
