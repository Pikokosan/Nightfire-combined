# E77 radio firmware updater for MicroPython.
#
# This talks to the E77 application protocol first, asks it to jump into the
# STM32 ROM UART bootloader, then writes a raw .bin image over the same UART.

from machine import UART, Pin
import time
import os

try:
    import ujson as json
except:
    import json

import e77_radio


ACK = 0x79
NACK = 0x1F

DEFAULT_ADDRESS = 0x08000000
DEFAULT_APP_BAUD = 115200
DEFAULT_BOOT_BAUD = 115200
DEFAULT_FIRMWARE_NAMES = (
    "/sd/e77_radio.bin",
    "/sd/e77_radio_latest.bin",
    "/sd/firmware/e77_radio.bin",
    "/sd/firmware/e77_radio_latest.bin",
    "/e77_radio.bin",
    "e77_radio.bin",
)
DEFAULT_MANIFESTS = (
    "/sd/latest.json",
    "/sd/firmware/latest.json",
    "/latest.json",
    "latest.json",
)
DEFAULT_UPDATE_FLAGS = (
    "/sd/radio_update.flag",
    "/sd/firmware/radio_update.flag",
    "/radio_update.flag",
    "radio_update.flag",
)


class E77BootloaderError(Exception):
    pass


def byte_value(data, index=0):
    value = data[index]
    if isinstance(value, int):
        return value
    return ord(value)


def int_to_be(value, size):
    return bytearray((value >> (8 * shift)) & 0xFF for shift in range(size - 1, -1, -1))


def file_exists(path):
    try:
        os.stat(path)
        return True
    except:
        return False


def dirname(path):
    slash = path.rfind("/")
    if slash < 0:
        return ""
    if slash == 0:
        return "/"
    return path[:slash]


def join_path(base, name):
    if not base or name.startswith("/"):
        return name
    if base.endswith("/"):
        return base + name
    return base + "/" + name


def parse_int(value, fallback=0):
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except:
        return fallback


def file_size(path):
    try:
        return os.stat(path)[6]
    except:
        return 0


def crc32_update(crc, data):
    crc ^= 0xFFFFFFFF
    for b in data:
        if not isinstance(b, int):
            b = ord(b)
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF


def crc32_file(path):
    crc = 0
    with open(path, "rb") as f:
        while True:
            data = f.read(256)
            if not data:
                break
            crc = crc32_update(crc, data)
    return crc & 0xFFFFFFFF


def read_manifest(path):
    with open(path, "r") as f:
        manifest = json.load(f)

    filename = manifest.get("filename", None)
    if not filename:
        raise E77BootloaderError("Firmware manifest has no filename")

    firmware_path = join_path(dirname(path), filename)
    return {
        "path": firmware_path,
        "address": parse_int(manifest.get("address", DEFAULT_ADDRESS), DEFAULT_ADDRESS),
        "crc32": parse_int(manifest.get("crc32", None), None),
        "version": manifest.get("version", ""),
    }


def find_firmware():
    for manifest_path in DEFAULT_MANIFESTS:
        if not file_exists(manifest_path):
            continue

        info = read_manifest(manifest_path)
        if file_exists(info["path"]):
            return info

    for path in DEFAULT_FIRMWARE_NAMES:
        if file_exists(path):
            return {
                "path": path,
                "address": DEFAULT_ADDRESS,
                "crc32": None,
                "version": "",
            }

    return None


def update_requested():
    for path in DEFAULT_UPDATE_FLAGS:
        if file_exists(path):
            return True
    return False


def clear_update_request():
    for path in DEFAULT_UPDATE_FLAGS:
        try:
            os.remove(path)
        except:
            pass


def reset_handler_from_bin(path):
    with open(path, "rb") as f:
        data = f.read(8)

    if len(data) < 8:
        raise E77BootloaderError("Firmware is too small")

    return (
        byte_value(data, 4) |
        (byte_value(data, 5) << 8) |
        (byte_value(data, 6) << 16) |
        (byte_value(data, 7) << 24)
    )


class STM32UartBootloader(object):
    def __init__(self, uart_id, tx_pin, rx_pin, app_baud=DEFAULT_APP_BAUD, boot_baud=DEFAULT_BOOT_BAUD):
        self.uart_id = uart_id
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.app_baud = app_baud
        self.boot_baud = boot_baud
        self.uart = None
        self.seq = 1

    def open_app_uart(self):
        self.uart = UART(
            self.uart_id,
            baudrate=self.app_baud,
            tx=Pin(self.tx_pin),
            rx=Pin(self.rx_pin),
        )
        self.drain()

    def open_boot_uart(self):
        try:
            self.uart = UART(
                self.uart_id,
                baudrate=self.boot_baud,
                bits=8,
                parity=0,
                stop=1,
                tx=Pin(self.tx_pin),
                rx=Pin(self.rx_pin),
            )
        except TypeError:
            raise E77BootloaderError("UART even parity is not supported on this port")
        self.drain()

    def drain(self):
        if self.uart is None:
            return

        deadline = time.ticks_add(time.ticks_ms(), 80)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            n = self.uart.any()
            if n:
                self.uart.read(n)
                deadline = time.ticks_add(time.ticks_ms(), 20)
            else:
                time.sleep_ms(2)

    def next_seq(self):
        seq = self.seq & 0xFF
        self.seq = (self.seq + 1) & 0xFF
        if self.seq == 0:
            self.seq = 1
        return seq

    def read_byte(self, timeout_ms):
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            data = self.uart.read(1)
            if data:
                return byte_value(data, 0)
            time.sleep_ms(1)
        return None

    def wait_ack(self, timeout_ms=1000):
        value = self.read_byte(timeout_ms)
        if value == ACK:
            return True
        if value == NACK:
            raise E77BootloaderError("STM32 bootloader returned NACK")
        raise E77BootloaderError("STM32 bootloader ACK timeout")

    def write(self, data):
        self.uart.write(data)

    def checksum_xor(self, data):
        checksum = 0
        for b in data:
            if not isinstance(b, int):
                b = ord(b)
            checksum ^= b
        return checksum & 0xFF

    def send_command(self, cmd, timeout_ms=1000):
        self.write(bytearray([cmd & 0xFF, (~cmd) & 0xFF]))
        self.wait_ack(timeout_ms)

    def send_address(self, address, timeout_ms=1000):
        data = int_to_be(address & 0xFFFFFFFF, 4)
        data.append(self.checksum_xor(data))
        self.write(data)
        self.wait_ack(timeout_ms)

    def request_radio_bootloader(self, timeout_ms=2000):
        self.open_app_uart()

        seq = self.next_seq()
        parser = e77_radio.E77FrameParser()
        self.write(e77_radio.build_frame(seq, e77_radio.CMD_BOOTLOADER))

        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            data = self.uart.read(64)
            if not data:
                time.sleep_ms(2)
                continue

            for b in data:
                frame = parser.feed(b)
                if not frame:
                    continue

                if frame.seq != seq or frame.cmd != e77_radio.CMD_BOOTLOADER:
                    continue

                if frame.flags & e77_radio.FLAG_ERROR:
                    raise E77BootloaderError("Radio rejected bootloader request")

                time.sleep_ms(800)
                return

        raise E77BootloaderError("No response to radio bootloader request")

    def sync(self, attempts=6):
        self.open_boot_uart()

        for _ in range(attempts):
            self.drain()
            self.write(b"\x7F")
            value = self.read_byte(500)
            if value == ACK:
                return
            time.sleep_ms(100)

        raise E77BootloaderError("STM32 bootloader sync failed")

    def mass_erase(self):
        try:
            self.send_command(0x44)
            self.write(b"\xFF\xFF\x00")
            self.wait_ack(20000)
            return
        except Exception as e:
            print("Extended erase failed:", e)

        self.send_command(0x43)
        self.write(b"\xFF\x00")
        self.wait_ack(20000)

    def write_memory_chunk(self, address, chunk):
        if len(chunk) < 1 or len(chunk) > 256:
            raise E77BootloaderError("Bad write chunk size")

        self.send_command(0x31)
        self.send_address(address)

        frame = bytearray([len(chunk) - 1])
        frame.extend(chunk)
        frame.append(self.checksum_xor(frame))
        self.write(frame)
        self.wait_ack(3000)

    def go(self, address):
        self.send_command(0x21)
        self.send_address(address)

    def flash_bin(self, firmware_path, address=DEFAULT_ADDRESS, expected_crc=None, progress_cb=None):
        size = file_size(firmware_path)
        if size < 8:
            raise E77BootloaderError("Firmware is too small")

        if expected_crc is not None:
            actual_crc = crc32_file(firmware_path)
            if actual_crc != (expected_crc & 0xFFFFFFFF):
                raise E77BootloaderError("Firmware CRC mismatch")

        reset_handler = reset_handler_from_bin(firmware_path)

        if progress_cb:
            progress_cb("Entering radio bootloader")
        self.request_radio_bootloader()

        if progress_cb:
            progress_cb("Syncing STM32 bootloader")
        self.sync()

        if progress_cb:
            progress_cb("Erasing radio flash")
        self.mass_erase()

        written = 0
        with open(firmware_path, "rb") as f:
            while written < size:
                chunk = f.read(256)
                if not chunk:
                    break

                self.write_memory_chunk(address + written, chunk)
                written += len(chunk)

                if progress_cb:
                    progress_cb("Writing %d/%d bytes" % (written, size))

        if progress_cb:
            progress_cb("Starting radio firmware")
        self.go(reset_handler)

        return {
            "path": firmware_path,
            "size": size,
            "address": address,
            "go_address": reset_handler,
        }


def flash_first_available(uart_id, tx_pin, rx_pin, app_baud=DEFAULT_APP_BAUD, boot_baud=DEFAULT_BOOT_BAUD, progress_cb=None):
    info = find_firmware()
    if not info:
        raise E77BootloaderError("No radio firmware found")

    updater = STM32UartBootloader(uart_id, tx_pin, rx_pin, app_baud, boot_baud)
    result = updater.flash_bin(
        info["path"],
        address=info.get("address", DEFAULT_ADDRESS),
        expected_crc=info.get("crc32", None),
        progress_cb=progress_cb,
    )
    result["version"] = info.get("version", "")
    return result
