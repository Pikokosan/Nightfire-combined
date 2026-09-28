from adc_scaling import ADCScaler, VoltageCalibration


STATE_INVALID = "invalid"
STATE_FAULT = "fault"
STATE_NORMAL = "normal"
STATE_OPEN = "open"


class BatteryVoltageSensor:
    def __init__(
        self,
        adc,
        adc_full_scale_mv=3300,
        divider_ratio=1.0,
        gain=1.0,
        offset_mv=0.0,
        samples=8,
        sample_delay_us=150,
        min_valid_mv=0,
        max_valid_mv=20000,
    ):
        self.scaler = ADCScaler(
            adc,
            adc_full_scale_mv=adc_full_scale_mv,
            samples=samples,
            sample_delay_us=sample_delay_us,
        )
        self.calibration = VoltageCalibration(divider_ratio, gain, offset_mv)
        self.min_valid_mv = int(min_valid_mv)
        self.max_valid_mv = int(max_valid_mv)

    def read(self):
        scaled = self.scaler.read_scaled()
        battery_mv = self.calibration.input_mv(scaled.get("adc_mv"))
        valid = bool(scaled.get("valid")) and battery_mv is not None
        if valid and (battery_mv < self.min_valid_mv or battery_mv > self.max_valid_mv):
            valid = False
            scaled["error"] = "battery voltage outside valid window"

        return {
            "valid": valid,
            "raw_adc": scaled.get("raw"),
            "adc_mv": scaled.get("adc_mv"),
            "battery_mv": None if battery_mv is None else int(battery_mv),
            "error": scaled.get("error", ""),
        }


class ChannelCalibration:
    def __init__(self, default_divider_ratio, default_gain=1.0, default_offset_mv=0.0):
        self.default_divider_ratio = float(default_divider_ratio)
        self.default_gain = float(default_gain)
        self.default_offset_mv = float(default_offset_mv)
        self._channels = {}

    def set_channel(self, channel, divider_ratio=None, gain=None, offset_mv=None):
        self._channels[int(channel)] = {
            "divider_ratio": self.default_divider_ratio if divider_ratio is None else float(divider_ratio),
            "gain": self.default_gain if gain is None else float(gain),
            "offset_mv": self.default_offset_mv if offset_mv is None else float(offset_mv),
        }

    def calibration_for(self, channel):
        cfg = self._channels.get(int(channel), None)
        if cfg is None:
            return VoltageCalibration(
                self.default_divider_ratio,
                self.default_gain,
                self.default_offset_mv,
            )
        return VoltageCalibration(cfg["divider_ratio"], cfg["gain"], cfg["offset_mv"])


class ChannelSenseSensor:
    def __init__(
        self,
        scanner,
        adc,
        calibration,
        adc_full_scale_mv=3300,
        samples=8,
        sample_delay_us=150,
    ):
        self.scanner = scanner
        self.scaler = ADCScaler(
            adc,
            adc_full_scale_mv=adc_full_scale_mv,
            samples=samples,
            sample_delay_us=sample_delay_us,
        )
        self.calibration = calibration

    def read_channel(self, channel, battery_mv=None):
        channel = int(channel)
        self.scanner.select(channel)
        scaled = self.scaler.read_scaled()
        cal = self.calibration.calibration_for(channel)
        sense_mv = cal.input_mv(scaled.get("adc_mv"))

        fraction = None
        if battery_mv and battery_mv > 0 and sense_mv is not None:
            fraction = float(sense_mv) / float(battery_mv)

        return {
            "channel": channel,
            "valid": bool(scaled.get("valid")) and sense_mv is not None,
            "raw_adc": scaled.get("raw"),
            "adc_mv": scaled.get("adc_mv"),
            "sense_mv": None if sense_mv is None else int(sense_mv),
            "battery_mv": battery_mv,
            "battery_fraction": fraction,
            "error": scaled.get("error", ""),
        }


class PretestCueSenseSensor:
    """
    Measure one selected cue through the board's low-current pre-test path.

    This intentionally clears the 74HC595 bank before and after every reading.
    It only enables /OE during the measurement window for the selected cue bit.
    """

    def __init__(
        self,
        outputs,
        pre_test_pin,
        adc,
        adc_full_scale_mv=3300,
        samples=8,
        sample_delay_us=150,
        pretest_settle_ms=5,
        output_settle_us=750,
    ):
        self.outputs = outputs
        self.pre_test_pin = pre_test_pin
        self.scaler = ADCScaler(
            adc,
            adc_full_scale_mv=adc_full_scale_mv,
            samples=samples,
            sample_delay_us=sample_delay_us,
        )
        self.pretest_settle_ms = int(pretest_settle_ms)
        self.output_settle_us = int(output_settle_us)

    def read_channel(self, channel, battery_mv=None):
        import time

        channel = int(channel)
        mask = 1 << (channel - 1)
        scaled = {}
        try:
            self.outputs.set_enabled(False)
            self.outputs.clear()
            self.pre_test_pin.value(1)
            if self.pretest_settle_ms:
                time.sleep_ms(self.pretest_settle_ms)
            self.outputs.digitalWriteMask(mask)
            self.outputs.set_enabled(True)
            if self.output_settle_us:
                time.sleep_us(self.output_settle_us)
            scaled = self.scaler.read_scaled()
        finally:
            self.outputs.set_enabled(False)
            self.outputs.clear()
            self.pre_test_pin.value(0)

        adc_mv = scaled.get("adc_mv")
        fraction = None
        if battery_mv and battery_mv > 0 and adc_mv is not None:
            fraction = float(adc_mv) / float(battery_mv)

        return {
            "channel": channel,
            "valid": bool(scaled.get("valid")) and adc_mv is not None,
            "raw_adc": scaled.get("raw"),
            "adc_mv": adc_mv,
            "sense_mv": None if adc_mv is None else int(adc_mv),
            "battery_mv": battery_mv,
            "battery_fraction": fraction,
            "error": scaled.get("error", ""),
        }


class ChannelClassifier:
    """
    Classify a channel from ADC-derived measurements.

    Defaults are intentionally threshold-based, not resistance-based. The load
    resistance equation should be added only after the exact test-current path is
    confirmed on hardware.
    """

    def __init__(
        self,
        open_max_fraction=0.05,
        short_min_fraction=0.80,
        invalid_is_open=True,
    ):
        self.open_max_fraction = float(open_max_fraction)
        self.short_min_fraction = float(short_min_fraction)
        self.invalid_is_open = bool(invalid_is_open)

    def classify(self, reading):
        if not reading or not reading.get("valid"):
            return STATE_OPEN if self.invalid_is_open else STATE_INVALID

        fraction = reading.get("battery_fraction")
        if fraction is None:
            return STATE_INVALID

        if fraction <= self.open_max_fraction:
            return STATE_OPEN
        if fraction >= self.short_min_fraction:
            return STATE_FAULT
        return STATE_NORMAL
