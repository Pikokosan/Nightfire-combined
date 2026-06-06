import time

import stm
from machine import Pin, SPI


FREQ_HZ = 915000000
BITRATE_BPS = 100000
FDEV_HZ = 50000
PREAMBLE_BITS = 5 * 8
MAX_PAYLOAD = 255

SYNC_WORD = b"\xC1\x94\xC1\x00\x00\x00\x00\x00"

REG_CRC_SEED = 0x06BC
REG_CRC_POLY = 0x06BE
REG_WHITEN_MSB = 0x06B8
REG_WHITEN_LSB = 0x06B9
REG_SYNC_WORD = 0x06C0
REG_BIT_SYNC = 0x06AC
REG_DRV_CTRL = 0x091F
REG_OCP = 0x08E7
REG_TX_CLAMP = 0x08D8
REG_XTA_TRIM = 0x0911
REG_XTB_TRIM = 0x0912

CMD_WRITE_REGISTER = 0x0D
CMD_READ_REGISTER = 0x1D
CMD_WRITE_BUFFER = 0x0E
CMD_READ_BUFFER = 0x1E
CMD_SET_SLEEP = 0x84
CMD_SET_STANDBY = 0x80
CMD_SET_RX = 0x82
CMD_SET_TX = 0x83
CMD_CALIBRATE = 0x89
CMD_SET_RF_FREQUENCY = 0x86
CMD_SET_PACKET_TYPE = 0x8A
CMD_SET_MODULATION_PARAMS = 0x8B
CMD_SET_PACKET_PARAMS = 0x8C
CMD_SET_TX_PARAMS = 0x8E
CMD_SET_BUFFER_BASE = 0x8F
CMD_SET_PA_CONFIG = 0x95
CMD_CLEAR_IRQ = 0x02
CMD_GET_IRQ = 0x12
CMD_SET_DIO_IRQ = 0x08
CMD_GET_RX_BUFFER_STATUS = 0x13
CMD_CALIBRATE_IMAGE = 0x98
CMD_SET_REGULATOR_MODE = 0x96
CMD_SET_RX_TX_FALLBACK = 0x93
CMD_STOP_RX_TIMER_ON_PREAMBLE = 0x9F

IRQ_TX_DONE = 1 << 0
IRQ_RX_DONE = 1 << 1
IRQ_CRC_ERR = 1 << 6
IRQ_TIMEOUT = 1 << 9
IRQ_ALL = 0x03FF

PACKET_GFSK = 0x00
STDBY_RC = 0x00
RAMP_40_US = 0x02
MOD_SHAPING_G_BT_1 = 0x0B
BW_234300 = 0x0A
PREAMBLE_DETECT_8_BITS = 0x04
ADDR_COMP_OFF = 0x00
PACKET_VARIABLE = 0x01
CRC_2_BYTES_INV = 0x06
DC_FREE_WHITENING = 0x01
USE_DCDC = 0x01
XTAL_DEFAULT_CAP_VALUE = 0x20


class E77Gfsk:
    def __init__(self):
        self.spi = SPI("SUBGHZ", baudrate=8000000, polarity=0, phase=0)
        self.txen = self._pin(("RF_TXEN", "PA6"))
        self.rxen = self._pin(("RF_RXEN", "PA7"))
        stm.subghz_cs(1)
        self.idle_switch()

    def _pin(self, names):
        for name in names:
            try:
                return Pin(name, Pin.OUT, value=0)
            except Exception:
                pass
        raise RuntimeError("RF switch pin not available")

    def _wait_busy(self, timeout_ms=1000):
        start = time.ticks_ms()
        while stm.subghz_is_busy():
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                raise RuntimeError("SUBGHZ busy timeout")
            time.sleep_us(50)

    def _xfer(self, data, wait_before=True):
        if wait_before:
            self._wait_busy()
        tx = bytes(data)
        rx = bytearray(len(tx))
        stm.subghz_cs(0)
        self.spi.write_readinto(tx, rx)
        stm.subghz_cs(1)
        self._wait_busy()
        return rx

    def cmd(self, op, payload=b"", wait_before=True):
        self._xfer(bytes([op]) + bytes(payload), wait_before=wait_before)

    def read_cmd(self, op, n):
        rx = self._xfer(bytes([op, 0x00]) + bytes(n))
        return bytes(rx[2:])

    def write_reg(self, addr, data):
        self.cmd(CMD_WRITE_REGISTER, bytes([(addr >> 8) & 0xFF, addr & 0xFF]) + bytes(data))

    def read_reg(self, addr, n=1):
        rx = self._xfer(bytes([CMD_READ_REGISTER, (addr >> 8) & 0xFF, addr & 0xFF, 0x00]) + bytes(n))
        return bytes(rx[4:])

    def set_switch_rx(self):
        self.txen(0)
        self.rxen(1)

    def set_switch_tx(self):
        self.rxen(0)
        self.txen(1)

    def idle_switch(self):
        self.txen(0)
        self.rxen(0)

    def init(self):
        self._radio_init()
        self.cmd(CMD_SET_STANDBY, bytes([STDBY_RC]))
        self.cmd(CMD_STOP_RX_TIMER_ON_PREAMBLE, b"\x00")
        self.cmd(CMD_SET_PACKET_TYPE, bytes([PACKET_GFSK]))
        self.write_reg(REG_BIT_SYNC, b"\x00")
        self._set_frequency(FREQ_HZ)
        self.cmd(CMD_SET_BUFFER_BASE, b"\x00\x00")
        self._set_power_20dbm()
        self._set_modulation()
        self._set_packet_params()
        self.write_reg(REG_SYNC_WORD, SYNC_WORD)
        self.write_reg(REG_CRC_SEED, b"\xFF\xFF")
        self.write_reg(REG_CRC_POLY, b"\x10\x21")
        self._set_whitening_seed(0x01FF)
        self.cmd(CMD_SET_RX_TX_FALLBACK, b"\x40")
        self.cmd(CMD_SET_DIO_IRQ, self._u16(IRQ_ALL) + self._u16(0) + self._u16(0) + self._u16(0))
        self.clear_irq()
        self.rx()

    def _radio_init(self):
        self.cmd(CMD_SET_STANDBY, bytes([STDBY_RC]), wait_before=False)
        self.write_reg(REG_XTA_TRIM, bytes([XTAL_DEFAULT_CAP_VALUE]))
        self.write_reg(REG_XTB_TRIM, bytes([XTAL_DEFAULT_CAP_VALUE]))
        self.write_reg(REG_DRV_CTRL, b"\x0E")
        self.cmd(CMD_SET_REGULATOR_MODE, bytes([USE_DCDC]))
        self.cmd(CMD_CALIBRATE, b"\x7F")
        self.cmd(CMD_SET_BUFFER_BASE, b"\x00\x00")
        self.cmd(CMD_SET_PA_CONFIG, b"\x07\x00\x01\x01")
        self.write_reg(REG_OCP, b"\x18")
        self.cmd(CMD_SET_TX_PARAMS, bytes([0x00, 0x03]))
        self.cmd(CMD_SET_DIO_IRQ, self._u16(IRQ_ALL) + self._u16(IRQ_ALL) + self._u16(0) + self._u16(0))
        self.clear_irq()

    def _set_frequency(self, freq_hz):
        # Calibration pair used by ST for 902-928 MHz.
        self.cmd(CMD_CALIBRATE_IMAGE, b"\xE1\xE9")
        chan = int((freq_hz << 25) // 32000000)
        self.cmd(CMD_SET_RF_FREQUENCY, chan.to_bytes(4, "big"))

    def _set_power_20dbm(self):
        clamp = self.read_reg(REG_TX_CLAMP, 1)[0] | (0x0F << 1)
        self.write_reg(REG_TX_CLAMP, bytes([clamp & 0xFF]))
        self.cmd(CMD_SET_PA_CONFIG, b"\x03\x05\x00\x01")
        self.write_reg(REG_OCP, b"\x38")
        self.write_reg(REG_DRV_CTRL, b"\x0E")
        self.cmd(CMD_SET_TX_PARAMS, bytes([0x14, RAMP_40_US]))

    def _set_modulation(self):
        br = int((32 * 32000000) // BITRATE_BPS)
        fd = int((FDEV_HZ << 25) // 32000000)
        data = br.to_bytes(3, "big")
        data += bytes([MOD_SHAPING_G_BT_1, BW_234300])
        data += fd.to_bytes(3, "big")
        self.cmd(CMD_SET_MODULATION_PARAMS, data)

    def _set_packet_params(self, payload_len=MAX_PAYLOAD):
        data = PREAMBLE_BITS.to_bytes(2, "big")
        data += bytes([
            PREAMBLE_DETECT_8_BITS,
            3 * 8,
            ADDR_COMP_OFF,
            PACKET_VARIABLE,
            payload_len & 0xFF,
            CRC_2_BYTES_INV,
            DC_FREE_WHITENING,
        ])
        self.cmd(CMD_SET_PACKET_PARAMS, data)

    def _set_whitening_seed(self, seed):
        msb = self.read_reg(REG_WHITEN_MSB, 1)[0] & 0xFE
        self.write_reg(REG_WHITEN_MSB, bytes([msb | ((seed >> 8) & 0x01)]))
        self.write_reg(REG_WHITEN_LSB, bytes([seed & 0xFF]))

    def _u16(self, value):
        return int(value & 0xFFFF).to_bytes(2, "big")

    def clear_irq(self, mask=IRQ_ALL):
        self.cmd(CMD_CLEAR_IRQ, self._u16(mask))

    def get_irq(self):
        data = self.read_cmd(CMD_GET_IRQ, 2)
        return (data[0] << 8) | data[1]

    def rx(self):
        self.clear_irq()
        self.set_switch_rx()
        self._set_packet_params(MAX_PAYLOAD)
        self.cmd(CMD_SET_RX, b"\xFF\xFF\xFF")

    def send(self, payload, timeout_ms=1000):
        if len(payload) > MAX_PAYLOAD:
            raise ValueError("payload too large")
        self.cmd(CMD_SET_STANDBY, bytes([STDBY_RC]))
        self.clear_irq()
        self.set_switch_tx()
        self._set_packet_params(len(payload))
        self.cmd(CMD_WRITE_BUFFER, b"\x00" + bytes(payload))
        self.cmd(CMD_SET_TX, b"\x00\xFF\xFF")
        start = time.ticks_ms()
        while True:
            irq = self.get_irq()
            if irq & IRQ_TX_DONE:
                self.clear_irq()
                self.rx()
                return True
            if irq & (IRQ_TIMEOUT | IRQ_CRC_ERR):
                self.clear_irq()
                self.rx()
                return False
            if time.ticks_diff(time.ticks_ms(), start) > timeout_ms:
                self.rx()
                return False
            time.sleep_ms(2)

    def poll(self):
        irq = self.get_irq()
        if not irq:
            return None
        if irq & IRQ_RX_DONE:
            status = self.read_cmd(CMD_GET_RX_BUFFER_STATUS, 2)
            length = status[0]
            offset = status[1]
            rx = self._xfer(bytes([CMD_READ_BUFFER, offset, 0x00]) + bytes(length))
            payload = bytes(rx[3:])
            self.clear_irq()
            self.rx()
            return payload
        if irq & IRQ_TIMEOUT:
            self.clear_irq()
            self.rx()
        return None
