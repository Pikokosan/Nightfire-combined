import time


ADC_U16_MAX = 65535


class ADCScaler:
    """Read raw ADC values and convert them to ADC-pin millivolts."""

    def __init__(
        self,
        adc,
        adc_full_scale_mv=3300,
        samples=8,
        sample_delay_us=150,
        valid_min_u16=0,
        valid_max_u16=ADC_U16_MAX,
    ):
        self.adc = adc
        self.adc_full_scale_mv = int(adc_full_scale_mv)
        self.samples = int(samples)
        self.sample_delay_us = int(sample_delay_us)
        self.valid_min_u16 = int(valid_min_u16)
        self.valid_max_u16 = int(valid_max_u16)

        if self.samples < 1:
            self.samples = 1

    def raw_to_adc_mv(self, raw):
        return (int(raw) * self.adc_full_scale_mv) / ADC_U16_MAX

    def read_raw(self):
        if self.adc is None:
            return {"valid": False, "raw": None, "error": "adc unavailable"}

        total = 0
        count = 0
        try:
            for i in range(self.samples):
                raw = int(self.adc.read_u16())
                if raw < 0 or raw > ADC_U16_MAX:
                    return {"valid": False, "raw": raw, "error": "adc out of range"}
                total += raw
                count += 1
                if self.sample_delay_us and i + 1 < self.samples:
                    time.sleep_us(self.sample_delay_us)
        except Exception as e:
            return {"valid": False, "raw": None, "error": str(e)}

        raw = total // count
        if raw < self.valid_min_u16 or raw > self.valid_max_u16:
            return {"valid": False, "raw": raw, "error": "adc outside valid window"}
        return {"valid": True, "raw": raw, "error": ""}

    def read_scaled(self):
        reading = self.read_raw()
        raw = reading.get("raw")
        adc_mv = None if raw is None else self.raw_to_adc_mv(raw)
        reading["adc_mv"] = adc_mv
        return reading


class VoltageCalibration:
    """
    Convert ADC-pin millivolts back to the measured node voltage.

    divider_ratio is Vadc / Vinput. For the channel sense divider this is:
    10k / (100k + 10k) = 0.090909...
    """

    def __init__(self, divider_ratio=1.0, gain=1.0, offset_mv=0.0):
        self.divider_ratio = float(divider_ratio)
        self.gain = float(gain)
        self.offset_mv = float(offset_mv)

    def input_mv(self, adc_mv):
        if adc_mv is None or self.divider_ratio <= 0:
            return None
        return (float(adc_mv) / self.divider_ratio) * self.gain + self.offset_mv
