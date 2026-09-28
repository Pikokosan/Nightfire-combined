import time
from machine import Pin


class HC4051Scanner:
    """
    Scan up to 18 channels using three 74HC4051 analog multiplexers.

    All muxes share S0/S1/S2. Each mux has an independent active-low /EN.
    This driver always disables all muxes before changing the address lines.
    """

    def __init__(
        self,
        s0_pin,
        s1_pin,
        s2_pin,
        en_pins,
        channel_count=18,
        settle_us=500,
    ):
        self.channel_count = int(channel_count)
        self.settle_us = int(settle_us)
        self.en = [Pin(pin, Pin.OUT, value=1) for pin in en_pins]
        self.s = [
            Pin(s0_pin, Pin.OUT, value=0),
            Pin(s1_pin, Pin.OUT, value=0),
            Pin(s2_pin, Pin.OUT, value=0),
        ]
        self.selected_channel = None
        self.disable_all()

    def disable_all(self):
        for pin in self.en:
            pin.value(1)
        self.selected_channel = None

    def _channel_to_mux(self, channel):
        channel = int(channel)
        if channel < 1 or channel > self.channel_count:
            raise ValueError("channel out of range")

        mux_index = (channel - 1) // 8
        mux_channel = (channel - 1) % 8
        if mux_index >= len(self.en):
            raise ValueError("mux index out of range")
        return mux_index, mux_channel

    def select_raw(self, mux_index, mux_channel):
        mux_index = int(mux_index)
        mux_channel = int(mux_channel)
        if mux_index < 0 or mux_index >= len(self.en):
            raise ValueError("mux index out of range")
        if mux_channel < 0 or mux_channel > 7:
            raise ValueError("mux channel out of range")

        self.disable_all()
        self.s[0].value(1 if (mux_channel & 0x01) else 0)
        self.s[1].value(1 if (mux_channel & 0x02) else 0)
        self.s[2].value(1 if (mux_channel & 0x04) else 0)
        if self.settle_us:
            time.sleep_us(self.settle_us)

        self.en[mux_index].value(0)
        self.selected_channel = None
        if self.settle_us:
            time.sleep_us(self.settle_us)
        return mux_index, mux_channel

    def select(self, channel):
        mux_index, mux_channel = self._channel_to_mux(channel)

        self.select_raw(mux_index, mux_channel)
        self.selected_channel = int(channel)
        return self.selected_channel
