import time
from machine import Pin


class ShiftRegister74HC59524:
    """
    Three daisy-chained 74HC595 devices treated as one 24-bit output bank.

    Channel/output bit 0 maps to the last bit shifted, which is normally QA on
    the first 74HC595 connected to the MCU. Shift order is configurable in
    hardware by wiring, so keep board-specific mapping outside this driver.
    """

    def __init__(
        self,
        data_pin,
        clock_pin,
        latch_pin,
        oe_pin=None,
        srclr_pin=None,
        q7s_pin=None,
        bit_count=24,
        used_outputs=18,
        clock_delay_us=0,
    ):
        self.bit_count = int(bit_count)
        self.used_outputs = int(used_outputs)
        self.clock_delay_us = int(clock_delay_us)
        self._mask = 0
        self._enabled = False

        # /OE is active-low and must come up disabled before any latch activity.
        self.oe = Pin(oe_pin, Pin.OUT, value=1) if oe_pin is not None else None
        self.srclr = Pin(srclr_pin, Pin.OUT, value=1) if srclr_pin is not None else None
        self.q7s = None
        if q7s_pin is not None:
            try:
                self.q7s = Pin(q7s_pin, Pin.IN, Pin.PULL_DOWN)
            except Exception:
                self.q7s = Pin(q7s_pin, Pin.IN)
        self.data = Pin(data_pin, Pin.OUT, value=0)
        self.clock = Pin(clock_pin, Pin.OUT, value=0)
        self.latch = Pin(latch_pin, Pin.OUT, value=0)

        if self.bit_count < 1:
            self.bit_count = 1
        if self.used_outputs < 1:
            self.used_outputs = 1
        if self.used_outputs > self.bit_count:
            self.used_outputs = self.bit_count

        self._used_mask = (1 << self.used_outputs) - 1

    def begin(self, enable_outputs=False):
        self.set_enabled(False)
        self.clear_storage()
        self.write_mask(0)
        if enable_outputs:
            self.set_enabled(True)

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)
        if self.oe is not None:
            self.oe.value(0 if self._enabled else 1)

    def is_enabled(self):
        return self._enabled

    def clear_storage(self):
        if self.srclr is None:
            return
        self.srclr.value(0)
        time.sleep_us(2)
        self.srclr.value(1)
        time.sleep_us(2)
        self.latch_pulse()

    def _pulse(self, pin):
        pin.value(1)
        if self.clock_delay_us:
            time.sleep_us(self.clock_delay_us)
        pin.value(0)
        if self.clock_delay_us:
            time.sleep_us(self.clock_delay_us)

    def latch_pulse(self):
        self._pulse(self.latch)

    def _shift_bit(self, value):
        self.data.value(1 if value else 0)
        self._pulse(self.clock)
        return self.q7s.value() if self.q7s is not None else 0

    def write_mask(self, mask):
        mask = int(mask) & self._used_mask

        for bit in range(self.bit_count - 1, -1, -1):
            self._shift_bit(mask & (1 << bit))

        self.latch_pulse()
        self._mask = mask
        return self._mask

    def digital_write(self, channel, value):
        channel = int(channel)
        if channel < 1 or channel > self.used_outputs:
            return self._mask

        bit = 1 << (channel - 1)
        if value:
            return self.write_mask(self._mask | bit)
        return self.write_mask(self._mask & ~bit)

    def digitalWrite(self, channel, value):
        return self.digital_write(channel, value)

    def digitalWriteMask(self, mask):
        return self.write_mask(mask)

    def clear(self):
        return self.write_mask(0)

    def current_mask(self):
        return self._mask

    def detect_chain_bits(self, max_bits=None):
        """
        Count shift-register bits by clocking one marker bit to Q7S.

        /OE is held disabled for the whole test. The storage register is left
        latched low before returning, so this can be exposed as a requested-only
        diagnostic without enabling any output.
        """
        if self.q7s is None:
            return {
                "valid": False,
                "chain_bits": 0,
                "registers": 0,
                "cue_slots": 0,
                "error": "q7s input not configured",
            }

        if max_bits is None:
            max_bits = self.bit_count + 8
        max_bits = int(max_bits)
        if max_bits < 1:
            max_bits = 1

        self.set_enabled(False)
        found_bits = 0
        try:
            self.clear_storage()
            for _ in range(max_bits):
                self._shift_bit(0)
            self.latch_pulse()
            self._mask = 0

            for clocks in range(1, max_bits + 1):
                if self._shift_bit(1 if clocks == 1 else 0):
                    found_bits = clocks
                    break

            self.write_mask(0)
        finally:
            self.set_enabled(False)
            self.clear()

        if not found_bits:
            return {
                "valid": False,
                "chain_bits": 0,
                "registers": 0,
                "cue_slots": 0,
                "error": "marker did not return on q7s",
            }

        return {
            "valid": True,
            "chain_bits": found_bits,
            "registers": (found_bits + 7) // 8,
            "cue_slots": found_bits,
            "error": "",
        }
