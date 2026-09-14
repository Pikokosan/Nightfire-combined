import os
import time

from nfcp import *


MAX_FILE_SIZE = 256 * 1024
CHUNK_TIMEOUT_MS = 8000

active = False
transfer_id = 0
target_path = ""
temp_path = ""
expected_size = 0
expected_checksum = 0
received_size = 0
checksum = 0
last_ms = 0
update_file = None


def close_file():
    global update_file

    try:
        if update_file:
            update_file.close()
    except Exception:
        pass
    update_file = None


def remove_if_exists(path):
    try:
        os.remove(path)
    except Exception:
        pass


def reset(remove_temp=False):
    global active, transfer_id, target_path, temp_path
    global expected_size, expected_checksum, received_size, checksum, last_ms

    close_file()
    if remove_temp and temp_path:
        remove_if_exists(temp_path)

    active = False
    transfer_id = 0
    target_path = ""
    temp_path = ""
    expected_size = 0
    expected_checksum = 0
    received_size = 0
    checksum = 0
    last_ms = 0


def safe_path(path):
    if not path or ".." in path or path.endswith("/"):
        return None
    if path[0] == "/":
        path = path[1:]
    if not path or len(path) > 64:
        return None
    return path


def begin(pkt, unique_id, busy, ack_cb, nack_cb):
    global active, transfer_id, target_path, temp_path
    global expected_size, expected_checksum, received_size, checksum, last_ms, update_file

    if busy:
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_REJECTED_ARMED)
        return

    data = pkt.get("data", b"")
    if len(data) < 11:
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_BAD_LENGTH)
        return

    new_transfer_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    size = int.from_bytes(data[2:6], "big") & 0xFFFFFFFF
    expected = int.from_bytes(data[6:10], "big") & 0xFFFFFFFF
    path_len = data[10]

    if len(data) != 11 + path_len:
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_BAD_LENGTH)
        return

    try:
        requested_path = bytes(data[11:]).decode()
    except Exception:
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_INVALID_VALUE)
        return

    requested_path = safe_path(requested_path)
    if requested_path is None or size < 1 or size > MAX_FILE_SIZE:
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_INVALID_VALUE)
        return

    reset(remove_temp=True)

    try:
        new_temp_path = requested_path + ".part"
        remove_if_exists(new_temp_path)
        update_file = open(new_temp_path, "wb")
    except Exception as e:
        print("OTA begin open failed:", e)
        reset(remove_temp=True)
        nack_cb(pkt, CMD_E77_UPDATE_BEGIN, NACK_INTERNAL_ERROR)
        return

    active = True
    transfer_id = new_transfer_id
    target_path = requested_path
    temp_path = new_temp_path
    expected_size = size
    expected_checksum = expected
    received_size = 0
    checksum = 0
    last_ms = time.ticks_ms()
    ack_cb(pkt, CMD_E77_UPDATE_BEGIN)


def write(pkt, unique_id, ack_cb, nack_cb):
    global received_size, checksum, last_ms

    data = pkt.get("data", b"")
    if len(data) < 6:
        nack_cb(pkt, CMD_E77_UPDATE_WRITE, NACK_BAD_LENGTH)
        return

    pkt_transfer_id = int.from_bytes(data[0:2], "big") & 0xFFFF
    offset = int.from_bytes(data[2:6], "big") & 0xFFFFFFFF
    chunk = data[6:]

    if not active or pkt_transfer_id != transfer_id:
        nack_cb(pkt, CMD_E77_UPDATE_WRITE, NACK_INVALID_VALUE)
        return

    if offset < received_size and offset + len(chunk) <= received_size:
        last_ms = time.ticks_ms()
        ack_cb(pkt, CMD_E77_UPDATE_WRITE)
        return

    if offset != received_size or received_size + len(chunk) > expected_size:
        nack_cb(pkt, CMD_E77_UPDATE_WRITE, NACK_INVALID_VALUE)
        return

    try:
        update_file.write(chunk)
    except Exception as e:
        print("OTA write failed:", e)
        reset(remove_temp=True)
        nack_cb(pkt, CMD_E77_UPDATE_WRITE, NACK_INTERNAL_ERROR)
        return

    for b in chunk:
        if not isinstance(b, int):
            b = ord(b)
        checksum = (checksum + b) & 0xFFFFFFFF

    received_size += len(chunk)
    last_ms = time.ticks_ms()
    ack_cb(pkt, CMD_E77_UPDATE_WRITE)


def end(pkt, unique_id, ack_cb, nack_cb):
    data = pkt.get("data", b"")
    if len(data) != 2:
        nack_cb(pkt, CMD_E77_UPDATE_END, NACK_BAD_LENGTH)
        return

    pkt_transfer_id = int.from_bytes(data, "big") & 0xFFFF
    if not active or pkt_transfer_id != transfer_id:
        nack_cb(pkt, CMD_E77_UPDATE_END, NACK_INVALID_VALUE)
        return

    if received_size != expected_size or checksum != expected_checksum:
        reset(remove_temp=True)
        nack_cb(pkt, CMD_E77_UPDATE_END, NACK_INVALID_VALUE)
        return

    try:
        close_file()
        remove_if_exists(target_path)
        os.rename(temp_path, target_path)
    except Exception as e:
        print("OTA finalize failed:", e)
        reset(remove_temp=True)
        nack_cb(pkt, CMD_E77_UPDATE_END, NACK_INTERNAL_ERROR)
        return

    reset(remove_temp=False)
    ack_cb(pkt, CMD_E77_UPDATE_END)


def abort(pkt, unique_id, ack_cb, nack_cb):
    reset(remove_temp=True)
    ack_cb(pkt, CMD_E77_UPDATE_ABORT)


def reboot(pkt, unique_id, busy, ack_cb, nack_cb):
    if busy:
        nack_cb(pkt, CMD_E77_UPDATE_REBOOT, NACK_REJECTED_ARMED)
        return False

    ack_cb(pkt, CMD_E77_UPDATE_REBOOT)
    return True


def poll_timeout():
    if active and time.ticks_diff(time.ticks_ms(), last_ms) > CHUNK_TIMEOUT_MS:
        reset(remove_temp=True)
