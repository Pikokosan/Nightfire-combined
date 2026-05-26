import argparse
import binascii
import os
import struct
import subprocess
import sys
import time

import serial


SOF = b"\xAA\x55"
VERSION = 1
MAX_PAYLOAD = 220

FLAG_RESPONSE = 0x02
FLAG_ERROR = 0x04

CMD_BOOTLOADER = 0x30


def crc16_ccitt(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def build_frame(seq, cmd, payload=b"", flags=0):
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")

    body = bytearray([4 + len(payload), VERSION, flags, seq & 0xFF, cmd])
    body.extend(payload)
    crc = crc16_ccitt(body)
    return bytearray(SOF) + body + crc.to_bytes(2, "big")


def read_frame(port, timeout):
    deadline = time.monotonic() + timeout
    state = 0

    while time.monotonic() < deadline:
        b = port.read(1)
        if not b:
            continue

        value = b[0]
        if state == 0:
            state = 1 if value == SOF[0] else 0
            continue

        if state == 1:
            if value != SOF[1]:
                state = 1 if value == SOF[0] else 0
                continue

            length_b = port.read(1)
            if not length_b:
                return None

            length = length_b[0]
            if length < 4 or length > MAX_PAYLOAD + 4:
                state = 0
                continue

            rest = port.read(length + 2)
            if len(rest) != length + 2:
                return None

            body = length_b + rest[:-2]
            rx_crc = int.from_bytes(rest[-2:], "big")
            if crc16_ccitt(body) != rx_crc:
                state = 0
                continue

            if body[1] != VERSION:
                state = 0
                continue

            return {
                "flags": body[2],
                "seq": body[3],
                "cmd": body[4],
                "payload": body[5:],
            }

    return None


def default_programmer_path():
    local = os.environ.get("STM32_PROGRAMMER_CLI")
    if local:
        return local

    return (
        r"C:\Users\hog28\AppData\Local\stm32cube\bundles"
        r"\programmer\2.22.0+st.1\bin\STM32_Programmer_CLI.exe"
    )


def reset_handler_from_bin(path, base_address=0x08000000):
    with open(path, "rb") as f:
        data = f.read(8)
    if len(data) < 8:
        raise RuntimeError("Binary is too small to contain a vector table.")
    return struct.unpack_from("<I", data, 4)[0]


def reset_handler_from_hex(path):
    bytes_by_addr = {}
    upper = 0

    with open(path, "r", encoding="ascii") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise RuntimeError("Invalid Intel HEX line: %r" % line)

            record = binascii.unhexlify(line[1:])
            size = record[0]
            address = (record[1] << 8) | record[2]
            rectype = record[3]
            data = record[4:4 + size]

            if rectype == 0x00:
                absolute = upper + address
                for i, value in enumerate(data):
                    bytes_by_addr[absolute + i] = value
            elif rectype == 0x04:
                upper = int.from_bytes(data, "big") << 16
            elif rectype == 0x01:
                break

    vector = bytes(bytes_by_addr.get(0x08000004 + i, 0) for i in range(4))
    if len(vector) != 4:
        raise RuntimeError("Could not read reset vector from HEX file.")
    return struct.unpack("<I", vector)[0]


def reset_handler_from_elf(path):
    with open(path, "rb") as f:
        elf = f.read()

    if len(elf) < 52 or elf[:4] != b"\x7FELF":
        raise RuntimeError("Not an ELF file.")
    if elf[4] != 1 or elf[5] != 1:
        raise RuntimeError("Only little-endian ELF32 files are supported.")

    e_shoff = struct.unpack_from("<I", elf, 32)[0]
    e_shentsize = struct.unpack_from("<H", elf, 46)[0]
    e_shnum = struct.unpack_from("<H", elf, 48)[0]
    e_shstrndx = struct.unpack_from("<H", elf, 50)[0]

    if e_shoff == 0 or e_shnum == 0:
        raise RuntimeError("ELF has no section table.")

    sections = []
    for i in range(e_shnum):
        off = e_shoff + (i * e_shentsize)
        sh = struct.unpack_from("<IIIIIIIIII", elf, off)
        sections.append(
            {
                "name_off": sh[0],
                "addr": sh[3],
                "offset": sh[4],
                "size": sh[5],
            }
        )

    shstr = sections[e_shstrndx]
    shstr_data = elf[shstr["offset"]:shstr["offset"] + shstr["size"]]

    def section_name(section):
        start = section["name_off"]
        end = shstr_data.find(b"\x00", start)
        if end < 0:
            return ""
        return shstr_data[start:end].decode("ascii", "replace")

    vector_section = None
    for section in sections:
        if section_name(section) == ".isr_vector":
            vector_section = section
            break

    if vector_section is None:
        for section in sections:
            if section["addr"] <= 0x08000004 < section["addr"] + section["size"]:
                vector_section = section
                break

    if vector_section is None:
        raise RuntimeError("Could not find vector table in ELF.")

    reset_offset = vector_section["offset"] + (0x08000004 - vector_section["addr"])
    return struct.unpack_from("<I", elf, reset_offset)[0]


def reset_handler_from_image(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".elf":
        return reset_handler_from_elf(path)
    if ext == ".hex":
        return reset_handler_from_hex(path)
    if ext == ".bin":
        return reset_handler_from_bin(path)
    raise RuntimeError("Cannot infer reset handler from %s. Use --go-address." % ext)


def enter_bootloader(port_name, baud, timeout):
    with serial.Serial(port_name, baud, timeout=0.05, write_timeout=2) as port:
        port.reset_input_buffer()
        port.reset_output_buffer()
        frame = build_frame(1, CMD_BOOTLOADER)
        print("Requesting STM32 ROM bootloader on %s..." % port_name)
        port.write(frame)
        port.flush()

        reply = read_frame(port, timeout)
        if not reply:
            raise RuntimeError("No response to bootloader request.")

        if reply["cmd"] != CMD_BOOTLOADER or not (reply["flags"] & FLAG_RESPONSE):
            raise RuntimeError("Unexpected bootloader response: %r" % reply)

        if reply["flags"] & FLAG_ERROR:
            raise RuntimeError("Device rejected bootloader request.")

        print("Bootloader request acknowledged.")


def run_programmer(programmer, port_name, firmware, baud, go_address):
    cmd = [
        programmer,
        "-c",
        "port=%s" % port_name,
        "br=%d" % baud,
        "P=EVEN",
        "-w",
        firmware,
        "-v",
    ]

    if go_address is not None:
        cmd.extend(["-g", "0x%08X" % go_address])

    print("Running:")
    print(" ".join('"%s"' % x if " " in x else x for x in cmd))
    return subprocess.call(cmd)


def main():
    parser = argparse.ArgumentParser(
        description="Update an E77 STM32WL radio firmware over its UART command port."
    )
    parser.add_argument("--port", required=True, help="Radio UART port, for example COM14 or COM15.")
    parser.add_argument("--baud", type=int, default=115200, help="Application UART baud rate.")
    parser.add_argument("--boot-timeout", type=float, default=2.0)
    parser.add_argument("--programmer", default=default_programmer_path())
    parser.add_argument("--programmer-baud", type=int, default=115200)
    parser.add_argument("--no-start", action="store_true", help="Do not start the application after programming.")
    parser.add_argument("--go-address", type=lambda value: int(value, 0), help="Override application start address.")
    parser.add_argument("firmware", help="Firmware image to write, usually e77_radio.elf or .bin/.hex.")
    args = parser.parse_args()

    if not os.path.exists(args.firmware):
        raise SystemExit("Firmware not found: %s" % args.firmware)

    if not os.path.exists(args.programmer):
        raise SystemExit("STM32_Programmer_CLI not found: %s" % args.programmer)

    enter_bootloader(args.port, args.baud, args.boot_timeout)

    # Give the ROM bootloader time to take ownership of USART2 after the app jumps.
    time.sleep(0.8)

    go_address = None
    if not args.no_start:
        go_address = args.go_address
        if go_address is None:
            go_address = reset_handler_from_image(args.firmware)
        print("Application reset handler: 0x%08X" % go_address)

    rc = run_programmer(
        args.programmer,
        args.port,
        args.firmware,
        args.programmer_baud,
        go_address=go_address,
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
