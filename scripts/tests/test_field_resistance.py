"""Host tests for field resistance calibration and NFCP payload parsing."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]


class ResistanceCalibrationTests(unittest.TestCase):
    def setUp(self):
        namespace = {}
        source = ROOT / "main" / "field_measurement.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        calibration = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ResistanceCalibration"
        )
        exec(
            compile(ast.Module(body=[calibration], type_ignores=[]), str(source), "exec"),
            namespace,
        )
        self.calibration_type = namespace["ResistanceCalibration"]

    def test_piecewise_reference_values_and_interpolation(self):
        calibration = self.calibration_type(((100, 0), (300, 20), (600, 47)))
        self.assertTrue(calibration.is_valid())
        self.assertEqual(calibration.deci_ohms_from_adc_mv(100), 0)
        self.assertEqual(calibration.deci_ohms_from_adc_mv(300), 20)
        self.assertEqual(calibration.deci_ohms_from_adc_mv(600), 47)
        self.assertEqual(calibration.deci_ohms_from_adc_mv(200), 10)
        self.assertEqual(calibration.deci_ohms_from_adc_mv(295), 20)

    def test_rejects_duplicate_adc_points(self):
        calibration = self.calibration_type(((100, 0), (100, 20)))
        self.assertFalse(calibration.is_valid())
        self.assertIsNone(calibration.deci_ohms_from_adc_mv(100))


class ResistancePayloadTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "parse_resistance_rsp"
        )
        protocol = SimpleNamespace(
            RESISTANCE_PAYLOAD_VERSION=1,
            RESISTANCE_UNIT_DECI_OHM=1,
            RESISTANCE_UNIT_ADC_MV=2,
            RESISTANCE_VALUE_INVALID=0xFFFE,
            RESISTANCE_VALUE_OPEN=0xFFFF,
        )
        namespace = {"nfcp": protocol, "MAX_CUE_COUNT": 64}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        self.parse = namespace["parse_resistance_rsp"]

    def test_parses_deci_ohms_and_sentinels(self):
        payload = bytes((1, 1, 4, 0, 0, 0, 0, 20, 0, 47, 0xFF, 0xFF))
        result = self.parse(payload, 4)
        self.assertEqual(result["unit"], "deci_ohm")
        self.assertEqual(result["values"], [0, 20, 47, None])

    def test_rejects_wrong_cue_count(self):
        payload = bytes((1, 1, 1, 0, 0, 0))
        self.assertIsNone(self.parse(payload, 24))


class ContinuityFromResistanceTests(unittest.TestCase):
    def setUp(self):
        source = ROOT / "main" / "master_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "continuity_from_resistance"
        )
        namespace = {"RESISTANCE_SHORT_MAX_DECI_OHM": 0}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        self.convert = namespace["continuity_from_resistance"]

    def test_only_calibrated_zero_ohms_is_short(self):
        result = {"unit": "deci_ohm", "values": [0, 1, 8, 20, None]}
        self.assertEqual(
            self.convert(result, 5),
            [False, True, True, True, False],
        )

    def test_raw_adc_zero_is_connected_until_calibrated(self):
        result = {"unit": "adc_mv", "values": [0, 158, None]}
        self.assertEqual(self.convert(result, 3), [True, True, False])


class ArmedResistanceMeasurementTests(unittest.TestCase):
    def make_namespace(self, armed=True, fire_mask=0, script_running=False):
        source = ROOT / "main" / "field_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in (
                "begin_resistance_measurement",
                "end_resistance_measurement",
            )
        ]
        calls = []
        namespace = {
            "ARMED": armed,
            "fire_out_mask": fire_mask,
            "script": SimpleNamespace(running=script_running),
            "set_armed_outputs": lambda value: calls.append(("armed", value)),
            "disable_measurement_frontend": lambda: calls.append(("disable", None)),
        }
        exec(
            compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
            namespace,
        )
        return namespace, calls

    def test_armed_scan_temporarily_safes_and_restores_outputs(self):
        namespace, calls = self.make_namespace(armed=True)
        restore = namespace["begin_resistance_measurement"]()
        namespace["end_resistance_measurement"](restore)
        self.assertTrue(restore)
        self.assertEqual(
            calls,
            [("armed", False), ("disable", None), ("armed", True)],
        )

    def test_scan_is_blocked_during_an_active_fire_pulse(self):
        namespace, calls = self.make_namespace(armed=True, fire_mask=1)
        with self.assertRaises(RuntimeError):
            namespace["begin_resistance_measurement"]()
        self.assertEqual(calls, [])

    def test_scan_is_blocked_while_a_script_runs(self):
        namespace, calls = self.make_namespace(armed=True, script_running=True)
        with self.assertRaises(RuntimeError):
            namespace["begin_resistance_measurement"]()
        self.assertEqual(calls, [])


class ArmVoltageCheckTests(unittest.TestCase):
    def make_namespace(self, raw_adc):
        source = ROOT / "main" / "field_main.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        functions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in (
                "read_short_check_mv",
                "check_arm_voltage",
            )
        ]
        calls = []

        class FakePin:
            def value(self, value=None):
                if value is not None:
                    calls.append(("pin", value))

        class FakeOutputs:
            def set_enabled(self, value):
                calls.append(("enabled", value))

            def clear(self):
                calls.append(("clear", None))

        namespace = {
            "ARM_VOLTAGE_CHECK_ENABLED": True,
            "ARM_VOLTAGE_CHECK_SETTLE_MS": 50,
            "ARM_VOLTAGE_CHECK_MIN_MV": 500,
            "ARM_VOLTAGE_CHECK_SAMPLES": 4,
            "ARM_CONTROL_ACTIVE_VALUE": 1,
            "ADC_FULL_SCALE_MV": 3300,
            "ADC_SAMPLE_DELAY_US": 0,
            "arm_check_mv": None,
            "arm_control": FakePin(),
            "sense_adc": SimpleNamespace(read_u16=lambda: raw_adc),
            "pre_test": FakePin(),
            "safe_led": FakePin(),
            "arm_led": FakePin(),
            "outputs": FakeOutputs(),
            "clear_fire_outputs": lambda: calls.append(("fire_clear", None)),
            "time": SimpleNamespace(sleep_ms=lambda delay: calls.append(("sleep", delay))),
        }
        exec(
            compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
            namespace,
        )
        return namespace, calls

    def test_arm_check_passes_when_short_check_has_voltage(self):
        namespace, calls = self.make_namespace(20000)
        self.assertTrue(namespace["check_arm_voltage"]())
        self.assertGreater(namespace["arm_check_mv"], 500)
        self.assertIn(("enabled", False), calls)
        self.assertNotIn(("enabled", True), calls)

    def test_arm_check_fails_when_short_check_is_low(self):
        namespace, calls = self.make_namespace(1000)
        self.assertFalse(namespace["check_arm_voltage"]())
        self.assertLess(namespace["arm_check_mv"], 500)
        self.assertIn(("pin", 1), calls)


if __name__ == "__main__":
    unittest.main()
