"""Host tests for field resistance calibration and NFCP payload parsing."""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1]


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
        namespace = {"RESISTANCE_SHORT_MAX_DECI_OHM": 5}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
        self.convert = namespace["continuity_from_resistance"]

    def test_calibrated_zero_ohms_is_not_good_continuity(self):
        result = {"unit": "deci_ohm", "values": [0, 2, 5, 6, 20, None]}
        self.assertEqual(
            self.convert(result, 6),
            [False, False, False, True, True, False],
        )

    def test_raw_adc_zero_is_connected_until_calibrated(self):
        result = {"unit": "adc_mv", "values": [0, 158, None]}
        self.assertEqual(self.convert(result, 3), [True, True, False])


if __name__ == "__main__":
    unittest.main()
