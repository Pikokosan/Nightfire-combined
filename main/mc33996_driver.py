# mc33996_driver.py
# Minimal MC33996 SPI driver for MicroPython (RP2040-friendly)
#
# The MC33996 uses one 24-bit SPI word per transaction. Configure SPI as
# CPOL=0, CPHA=1: SI is sampled on SCLK falling edge, SO changes on rising edge.

import time
from machine import Pin

CMD_ON_OFF = 0x00
CMD_OPENLOAD_EN = 0x04
CMD_GLOBAL_SHUTDOWN_RETRY = 0x08
CMD_SFPD = 0x0C
CMD_PWM_EN = 0x10
CMD_PWM_AND_OR = 0x14
CMD_RESET = 0x18

CMD_NULL = 0x0000
CMD_ALL_ON = 0xFFFF

ANY_FAULT = 0x80
OVERVOLTAGE_FAULT = 0x40

FAULT_FILTER_US = 600  # Datasheet max open/short fault filter is 450us.
SPI_SETTLE_US = 20


class MC33996:
    def __init__(self, spi, cs_pin: int, reset_pin: int, cs_hold_us: int = 0):
        """
        spi: machine.SPI instance
        cs_pin: GPIO for CS
        reset_pin: GPIO for RESET
        cs_hold_us: optional delay to make CS pulses visible on a scope/logic analyzer
        """
        self.spi = spi
        self.cs = Pin(cs_pin, Pin.OUT, value=1)
        self.reset = Pin(reset_pin, Pin.OUT, value=0)

        self._outputCache = 0x0000
        self._openLoadEnableCache = 0x0000
        self._cs_hold_us = cs_hold_us

    def begin(self, enable_open_load: bool = True):
        self.cs.value(1)
        self.reset.value(0)
        time.sleep_ms(50)
        self.reset.value(1)
        time.sleep_ms(50)
        self.resetDevice()
        self.digitalWriteMask(0x0000)
        self.enableContinuityDetection(enable_open_load)

    def resetDevice(self):
        self._outputCache = 0x0000
        self._openLoadEnableCache = 0x0000
        return self.transfer(CMD_RESET, CMD_NULL)

    def _word(self, reg: int, value: int) -> bytes:
        return bytes([reg & 0xFF, (value >> 8) & 0xFF, value & 0xFF])

    def _cs_low(self):
        self.cs.value(0)
        if self._cs_hold_us:
            time.sleep_us(self._cs_hold_us)

    def _cs_high(self):
        if self._cs_hold_us:
            time.sleep_us(self._cs_hold_us)
        self.cs.value(1)

    def write(self, reg: int, value: int):
        return self.transfer(reg, value)

    def transfer(self, reg: int, value: int):
        """
        Send one 24-bit command word and return the simultaneous 24-bit SO word.

        SO reports the fault status captured when CS falls. The command just sent
        takes effect when CS rises, so callers that change a detection register
        must wait and read again for the resulting fault state.
        """
        tx = self._word(reg, value)
        rx = bytearray(3)
        self._cs_low()
        self.spi.write_readinto(tx, rx)
        self._cs_high()
        time.sleep_us(SPI_SETTLE_US)
        return rx

    def decodeFaultWord(self, rx):
        status = rx[0] & 0xFF
        fault_mask = ((rx[1] & 0xFF) << 8) | (rx[2] & 0xFF)
        return {
            "status": status,
            "any_fault": bool(status & ANY_FAULT),
            "overvoltage": bool(status & OVERVOLTAGE_FAULT),
            "fault_mask": fault_mask,
            "raw": bytes(rx),
        }

    def readFaults(self, settle_us: int = FAULT_FILTER_US, fresh: bool = True):
        """
        Return decoded SO fault status without changing the programmed outputs.

        fault_mask bit 0 maps to OUT0, bit 15 maps to OUT15.
        A set bit means fault/open for that output; a clear bit means no fault.
        """
        if fresh:
            # Fault bits are latched in the SO response and cleared on CS rising.
            # Read once to clear stale latches, then wait for the fault filter
            # before reading the current output state.
            self.transfer(CMD_ON_OFF, self._outputCache)
        if settle_us:
            time.sleep_us(settle_us)
        rx = self.transfer(CMD_ON_OFF, self._outputCache)
        return self.decodeFaultWord(rx)

    def digitalWrite(self, ch: int, value: int):
        """
        ch: 1..16
        value: 0/1
        """
        if ch < 1 or ch > 16:
            return

        bit = 1 << (ch - 1)
        if value:
            self._outputCache |= bit
        else:
            self._outputCache &= ~bit

        self.write(CMD_ON_OFF, self._outputCache)

    def digitalWriteMask(self, mask16: int):
        self._outputCache = mask16 & 0xFFFF
        return self.write(CMD_ON_OFF, self._outputCache)

    def enableOpenLoadDetect(self, mask16: int = CMD_ALL_ON):
        self._openLoadEnableCache = mask16 & 0xFFFF
        return self.write(CMD_OPENLOAD_EN, self._openLoadEnableCache)

    def disableOpenLoadDetect(self):
        self._openLoadEnableCache = 0x0000
        return self.write(CMD_OPENLOAD_EN, 0x0000)

    def enableContinuityDetection(self, enable: bool = True):
        if enable:
            return self.enableOpenLoadDetect(CMD_ALL_ON)
        return self.disableOpenLoadDetect()

    def readOpenLoad(self):
        """
        Returns (status_byte, openload_16).

        Compatibility wrapper for the application. The returned mask is the
        MC33996 SO fault mask: 1 = fault/open, 0 = no fault.
        """
        faults = self.readFaults()
        return faults["status"], faults["fault_mask"]
