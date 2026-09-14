# main.py
# NFCP Master Controller GUI
# Waveshare ESP32-S3-LCD-4.3
# LVGL 9.2.2
# Uses shared nfcp.py for packet protocol

from machine import UART, Pin, I2C
import time
import os
import lvgl as lv
import display_driver
import ujson as json
import urandom as random
import nfcp
import e77_radio
from adafruit_trellis import Trellis

try:
    import fs_driver
except Exception as e:
    fs_driver = None
    print("LVGL FS driver failed:", e)

DEFAULT_CUE_COUNT = 16
MAX_CUE_COUNT = 64
MODULE_CONTINUITY_AUTO_CHECK_MS = 4000
DISCOVER_IDLE_TIMEOUT_MS = 3000
DISCOVER_MAX_SCAN_MS = 30000
DISCOVER_BROADCAST_REPEAT_MS = 700
SPEED_TEST_TIMEOUT_MS = 2000
ACK_RETRY_ATTEMPTS = 2
ACK_RETRY_DELAY_MS = 100
E77_UPDATE_CHUNK_SIZE = 160
SCRIPT_LOAD_CHUNK_ENTRIES = 32
SCRIPT_START_DELAY_MS = 2000
SCRIPT_START_BROADCAST_REPEATS = 3
SCRIPT_TIME_SYNC_REPEATS = 3
SCRIPT_TIME_SYNC_GAP_MS = 20
SCRIPT_HEARTBEAT_MS = 200
TRELLIS_ENABLED = True
TRELLIS_I2C_ID = 0
TRELLIS_SCL_PIN = 9
TRELLIS_SDA_PIN = 8
TRELLIS_ADDRESS = 0x70
TRELLIS_I2C_FREQ = 400000
TRELLIS_POLL_MS = 4
MANUAL_FIRE_DEBOUNCE_MS = 180
MANUAL_FIRE_BLINK_MS = 250
TRELLIS_STEP_DEBOUNCE_MS = 60
TRELLIS_LED_SWEEP_DELAY_MS = 45
SCRIPT_TIMER_FONT_PATH = "timer_96.bin"

SRC_ID = 0x01
DEVICE_DB_PATH = "nfcp_devices.json"
SETTINGS_PATH = "nfcp_settings.json"

RADIO_NETWORK_ID = e77_radio.DEFAULT_NETWORK_ID
RADIO_CHANNEL = 40
RADIO_APP_BAUD = 256000
RADIO_BOOTLOADER_BAUD = 115200

UART_ID = 2
BAUDRATE = RADIO_APP_BAUD
TX_PIN = 15
RX_PIN = 16

uart = UART(
    UART_ID,
    baudrate=BAUDRATE,
    tx=Pin(TX_PIN),
    rx=Pin(RX_PIN)
)

nfcp_tx = nfcp.NFCP(SRC_ID)
nfcp_rx = nfcp.NFCPStreamParser()
radio_rx = e77_radio.E77FrameParser()
radio_seq = 1
_last_packets = []
_last_radio_links = {}
lvgl_fs_drv = None
script_clock_font = None

try:
    import sdcard_driver
except Exception as e:
    sdcard_driver = None
    print("SD driver failed:", e)


def load_screen(scr):
    if hasattr(lv, "screen_load"):
        lv.screen_load(scr)
    else:
        lv.scr_load(scr)


def get_script_clock_font():
    global lvgl_fs_drv
    global script_clock_font

    if script_clock_font is not None:
        return script_clock_font

    if fs_driver is None:
        return None

    try:
        os.stat(SCRIPT_TIMER_FONT_PATH)
    except Exception as e:
        print("Script timer font missing:", e)
        return None

    try:
        if lvgl_fs_drv is None:
            lvgl_fs_drv = lv.fs_drv_t()
            fs_driver.fs_register(lvgl_fs_drv, "S")

        script_clock_font = lv.binfont_create("S:" + SCRIPT_TIMER_FONT_PATH)
        if script_clock_font is None:
            print("Script timer font load returned None")
    except Exception as e:
        script_clock_font = None
        print("Script timer font load failed:", e)

    return script_clock_font


def delete_device_db_on_startup():
    try:
        os.remove("nfcp_devices.json")
        print("Deleted nfcp_devices.json on startup")
    except OSError:
        print("No nfcp_devices.json to delete")
    except Exception as e:
        print("Failed to delete nfcp_devices.json:", e)


def next_radio_seq():
    global radio_seq

    seq = radio_seq & 0xFF
    radio_seq = (radio_seq + 1) & 0xFF
    if radio_seq == 0:
        radio_seq = 1
    return seq


def radio_write_nfcp(pkt):
    uart.write(e77_radio.build_send_nfcp(next_radio_seq(), pkt))


def uart_read_some(max_bytes=256):
    n = uart.any()
    if not n:
        return b""
    if n > max_bytes:
        n = max_bytes
    return uart.read(n) or b""


def drain_uart():
    while uart.any():
        uart.read()


def local_radio_command(cmd, payload=b"", timeout_ms=1000):
    global radio_rx

    drain_uart()
    radio_rx = e77_radio.E77FrameParser()

    seq = next_radio_seq()
    uart.write(e77_radio.build_frame(seq, cmd, payload))

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        data = uart_read_some(64)
        if not data:
            time.sleep_ms(5)
            continue

        for b in data:
            frame = radio_rx.feed(b)
            if not frame:
                continue
            if frame.seq == seq:
                return frame

    return None


def radio_ping(timeout_ms=1000):
    frame = local_radio_command(e77_radio.CMD_PING, b"", timeout_ms)
    return bool(frame and frame.cmd == e77_radio.CMD_PONG and not (frame.flags & e77_radio.FLAG_ERROR))


def set_local_radio_network(network_id, timeout_ms=1000):
    frame = local_radio_command(
        e77_radio.CMD_SET_NETWORK,
        (network_id & 0xFFFF).to_bytes(2, "big"),
        timeout_ms
    )

    return bool(
        frame and
        frame.cmd == e77_radio.CMD_SET_NETWORK and
        not (frame.flags & e77_radio.FLAG_ERROR)
    )


def set_local_radio_channel(channel, timeout_ms=1000):
    frame = local_radio_command(
        e77_radio.CMD_SET_CHANNEL,
        bytes([channel & 0xFF]),
        timeout_ms
    )

    return bool(
        frame and
        frame.cmd == e77_radio.CMD_SET_CHANNEL and
        not (frame.flags & e77_radio.FLAG_ERROR)
    )


def parse_channel_scan_payload(payload):
    results = []
    if not payload:
        return results

    count = payload[0]
    index = 1
    entry_size = 7
    if len(payload) >= 1 + count * 10:
        entry_size = 10

    for _ in range(count):
        if index + entry_size > len(payload):
            break
        channel = payload[index]
        avg = e77_radio.int_from_be(payload[index + 1:index + 3], signed=True)
        min_rssi = e77_radio.int_from_be(payload[index + 3:index + 5], signed=True)
        max_rssi = e77_radio.int_from_be(payload[index + 5:index + 7], signed=True)
        result = {
            "channel": channel,
            "avg": avg,
            "min": min_rssi,
            "max": max_rssi,
            "count": 0,
            "busy": 0,
            "score": 0
        }
        if entry_size >= 10:
            result["count"] = payload[index + 7]
            result["busy"] = payload[index + 8]
            result["score"] = payload[index + 9]
        results.append(result)
        index += entry_size

    return results


def scan_local_radio_channels(timeout_ms=8000):
    frame = local_radio_command(e77_radio.CMD_SCAN_CHANNELS, b"", timeout_ms)
    if not frame:
        return []
    if frame.cmd != e77_radio.CMD_SCAN_CHANNELS or (frame.flags & e77_radio.FLAG_ERROR):
        return []
    return parse_channel_scan_payload(frame.payload)


def get_local_radio_stats(timeout_ms=1000):
    frame = local_radio_command(e77_radio.CMD_GET_STATS, b"", timeout_ms)
    if not frame:
        return None
    if frame.cmd != e77_radio.CMD_GET_STATS or (frame.flags & e77_radio.FLAG_ERROR):
        return None
    return e77_radio.parse_stats_payload(frame.payload)


def ensure_local_radio_channel(channel, timeout_ms=1000):
    try:
        channel = int(channel)
    except:
        return False, "Invalid saved channel"

    if channel < 0 or channel > 100:
        return False, "Invalid saved channel"

    stats = get_local_radio_stats(timeout_ms)
    if stats:
        try:
            actual = int(stats.get("channel", -1))
        except:
            actual = -1

        if actual == channel:
            return True, None

    if set_local_radio_channel(channel, timeout_ms):
        if stats:
            return True, "Bridge channel corrected to %d" % channel
        return True, "Bridge channel set to %d" % channel

    return False, "Bridge channel mismatch"


def clear_local_radio_stats(timeout_ms=1000):
    frame = local_radio_command(e77_radio.CMD_CLEAR_STATS, b"", timeout_ms)
    if not frame:
        return False
    return frame.cmd == e77_radio.CMD_CLEAR_STATS and not (frame.flags & e77_radio.FLAG_ERROR)


def get_local_radio_info(timeout_ms=1000):
    frame = local_radio_command(e77_radio.CMD_GET_INFO, b"", timeout_ms)
    if not frame:
        return None
    if frame.cmd != e77_radio.CMD_GET_INFO or (frame.flags & e77_radio.FLAG_ERROR):
        return None
    return e77_radio.parse_get_info(frame.payload)


def check_radio_ready(network_id, channel=None):
    if not radio_ping(1000):
        return False

    if not set_local_radio_network(network_id, 1000):
        return False

    if channel is not None and not set_local_radio_channel(channel, 1000):
        return False

    return radio_ping(1000)


def trellis_pressed_keys(trellis):
    pressed = []
    for key in range(16):
        if trellis.is_key_pressed(key):
            pressed.append(key)
    return pressed


def trellis_sweep_leds(trellis):
    trellis.clear()
    trellis.write_display()

    for led in range(16):
        trellis.clear()
        trellis.set_led(led)
        trellis.write_display()
        time.sleep_ms(TRELLIS_LED_SWEEP_DELAY_MS)

    trellis.clear()
    trellis.write_display()


def init_trellis_boot_check():
    if not TRELLIS_ENABLED:
        return None, None

    try:
        i2c_bus = I2C(
            TRELLIS_I2C_ID,
            scl=Pin(TRELLIS_SCL_PIN),
            sda=Pin(TRELLIS_SDA_PIN),
            freq=TRELLIS_I2C_FREQ
        )
    except Exception as e:
        return None, "Trellis I2C init failed: %s" % str(e)

    try:
        devices = i2c_bus.scan()
    except Exception as e:
        return None, "Trellis I2C scan failed: %s" % str(e)

    if TRELLIS_ADDRESS not in devices:
        return None, "Trellis not found at 0x%02X" % TRELLIS_ADDRESS

    try:
        trellis = Trellis(i2c_bus, TRELLIS_ADDRESS)
        trellis.begin()
    except Exception as e:
        return None, "Trellis init failed: %s" % str(e)

    try:
        trellis.read_switches()
        time.sleep_ms(20)
        trellis.read_switches()
        pressed = trellis_pressed_keys(trellis)
    except Exception as e:
        return trellis, "Trellis button read failed: %s" % str(e)

    if pressed:
        return trellis, "Trellis buttons held on boot: %s" % (
            ", ".join(str(key) for key in pressed)
        )

    try:
        trellis_sweep_leds(trellis)
    except Exception as e:
        return trellis, "Trellis LED sweep failed: %s" % str(e)

    return trellis, None


def load_device_db(path=DEVICE_DB_PATH):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except:
        return {
            "generated_epoch": None,
            "device_count": 0,
            "devices": []
        }


def save_device_db(db, path=DEVICE_DB_PATH):
    db["generated_epoch"] = time.time()
    db["device_count"] = len(db.get("devices", []))

    with open(path, "w") as f:
        json.dump(db, f)


def clear_device_db(path=DEVICE_DB_PATH):
    save_device_db({
        "generated_epoch": time.time(),
        "device_count": 0,
        "devices": []
    }, path)


def load_app_settings(path=SETTINGS_PATH):
    try:
        with open(path, "r") as f:
            settings = json.load(f)
    except:
        settings = {}

    try:
        radio_network_id = int(settings.get("radio_network_id", RADIO_NETWORK_ID)) & 0xFFFF
    except:
        radio_network_id = RADIO_NETWORK_ID

    try:
        radio_channel = int(settings.get("radio_channel", RADIO_CHANNEL))
    except:
        radio_channel = RADIO_CHANNEL

    if radio_channel < 0 or radio_channel > 100:
        radio_channel = RADIO_CHANNEL

    if radio_network_id == 1 and RADIO_NETWORK_ID != 1:
        radio_network_id = RADIO_NETWORK_ID
        settings["radio_network_id"] = radio_network_id
        try:
            save_app_settings(settings, path)
        except:
            pass

    return {
        "auto_module_continuity": bool(settings.get("auto_module_continuity", True)),
        "color_cues_without_script": bool(settings.get("color_cues_without_script", True)),
        "radio_network_id": radio_network_id,
        "radio_channel": radio_channel
    }


def save_app_settings(settings, path=SETTINGS_PATH):
    with open(path, "w") as f:
        json.dump(settings, f)


def ack_code_text(code):
    if code is None:
        return "timeout"
    if code == nfcp.ACK_OK:
        return "ok"
    if code == nfcp.NACK_REJECTED_ARMED:
        return "disarmed"
    if code == nfcp.NACK_BAD_LENGTH:
        return "bad length"
    if code == nfcp.NACK_INVALID_VALUE:
        return "invalid script"
    if code == nfcp.NACK_INTERNAL_ERROR:
        return "internal error"
    if code == nfcp.NACK_BUSY:
        return "busy"
    return "code %d" % code


def index_db_by_unique(db):
    out = {}
    for d in db.get("devices", []):
        if "unique_id" in d:
            out[d["unique_id"]] = d
    return out


def parse_discover_rsp(src_unique, data):
    if len(data) < 10:
        return None

    return {
        "src_unique": "0x%08X" % src_unique,
        "group_id": "0x%04X" % int.from_bytes(data[0:2], "big"),
        "unique_id": "0x%08X" % int.from_bytes(data[2:6], "big"),
        "cue_count": data[6],
        "device_type": "0x%02X" % data[7],
        "proto_ver": "0x%02X" % data[8],
        "flags": "0x%02X" % data[9],
        "last_seen_epoch": time.time()
    }


def parse_continuity_rsp(data, cue_count):
    try:
        cue_count = int(cue_count or DEFAULT_CUE_COUNT)
    except:
        cue_count = DEFAULT_CUE_COUNT

    if cue_count < 1:
        cue_count = 1
    if cue_count > MAX_CUE_COUNT:
        cue_count = MAX_CUE_COUNT

    nbytes = (cue_count + 7) // 8
    if len(data) == nbytes:
        open_mask = int.from_bytes(data, "big")
        out = [False] * cue_count
        for i in range(cue_count):
            out[i] = not bool(open_mask & (1 << i))
        return out

    # Legacy 16-cue response: cue_ok_mask(1) | openload16(2) | mode(1).
    if len(data) >= 3:
        openload16 = int.from_bytes(data[1:3], "big")
        ok16 = (~openload16) & 0xFFFF
        out = [False] * cue_count
        n = min(cue_count, 16)
        for i in range(n):
            out[i] = bool(ok16 & (1 << i))
        return out

    return [False] * cue_count


def read_text_field(data, index):
    if index >= len(data):
        return "", index
    n = data[index]
    index += 1
    if index + n > len(data):
        return "", len(data)
    try:
        text = bytes(data[index:index + n]).decode()
    except:
        text = ""
    return text, index + n


def parse_info_rsp(data):
    index = 0
    app_name, index = read_text_field(data, index)
    app_version, index = read_text_field(data, index)
    radio_version, index = read_text_field(data, index)
    script_status, index = read_text_field(data, index)
    hardware_unique_id, index = read_text_field(data, index)
    configured_unique_id, index = read_text_field(data, index)
    return {
        "app_name": app_name,
        "app_version": app_version,
        "radio_version": radio_version,
        "script_status": script_status,
        "hardware_unique_id": hardware_unique_id,
        "configured_unique_id": configured_unique_id
    }


def poll_responses(max_packets=8):
    global _last_packets

    packets = []
    data = uart_read_some(256)
    if not data:
        return packets

    for b in data:
        frame = radio_rx.feed(b)
        if not frame:
            continue
        if frame.cmd != e77_radio.CMD_NFCP_RX:
            continue
        rx_info = e77_radio.parse_nfcp_rx(frame.payload)
        if not rx_info:
            continue
        nfcp_data = rx_info.get("nfcp_frame", b"")
        radio_rssi = rx_info.get("rssi", None)
        radio_snr = rx_info.get("snr", None)

        for nfcp_b in nfcp_data:
            pkt = nfcp_rx.feed(nfcp_b)
            if not pkt:
                continue

            if pkt.get("frame_type") != nfcp.SOF_RESPONSE:
                continue

            out = {
                "sof2": nfcp.SOF2_RESPONSE,
                "src_unique": pkt.get("src", 0),
                "cmd": pkt.get("cmd", 0),
                "data": pkt.get("data", b""),
                "radio_rssi": radio_rssi,
                "radio_snr": radio_snr
            }
            packets.append(out)
            _last_packets.append(out)

            if len(_last_packets) > 20:
                _last_packets = _last_packets[-20:]
            if len(packets) >= max_packets:
                break
        if len(packets) >= max_packets:
            break

    return packets


def get_last_packets():
    return _last_packets


def update_radio_link_from_packet(pkt):
    uid = pkt.get("src_unique", None)
    if uid is None:
        return

    rssi = pkt.get("radio_rssi", None)
    snr = pkt.get("radio_snr", None)

    if rssi is None and snr is None:
        return

    key = "0x%08X" % (uid & 0xFFFFFFFF)
    _last_radio_links[key] = {
        "radio_rssi": rssi,
        "radio_snr": snr,
        "radio_last_epoch": time.time()
    }


def get_last_radio_link(uid_text):
    return _last_radio_links.get(uid_text, None)


def discover(idle_ms=DISCOVER_IDLE_TIMEOUT_MS, max_scan_ms=DISCOVER_MAX_SCAN_MS):
    drain_uart()

    nonce = random.getrandbits(16)
    radio_write_nfcp(nfcp_tx.build_discover_broadcast(nonce))

    db = load_device_db()
    idx = index_db_by_unique(db)
    discovered_uids = []
    start_ms = time.ticks_ms()
    last_packet_ms = start_ms
    last_broadcast_ms = start_ms
    max_deadline = time.ticks_add(start_ms, max_scan_ms)

    while time.ticks_diff(max_deadline, time.ticks_ms()) > 0:
        now_ms = time.ticks_ms()
        if time.ticks_diff(now_ms, last_broadcast_ms) >= DISCOVER_BROADCAST_REPEAT_MS:
            radio_write_nfcp(nfcp_tx.build_discover_broadcast(nonce))
            last_broadcast_ms = now_ms

        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_DISCOVER_RSP:
                continue

            info = parse_discover_rsp(pkt["src_unique"], pkt["data"])
            if info:
                update_radio_link_from_packet(pkt)
                if pkt.get("radio_rssi", None) is not None:
                    info["radio_rssi"] = pkt["radio_rssi"]
                if pkt.get("radio_snr", None) is not None:
                    info["radio_snr"] = pkt["radio_snr"]
                info["radio_last_epoch"] = time.time()
                info["module_online"] = True
                info["module_last_seen_epoch"] = time.time()
                existing = idx.get(info["unique_id"], {})
                existing.update(info)
                idx[info["unique_id"]] = existing
                if info["unique_id"] not in discovered_uids:
                    discovered_uids.append(info["unique_id"])
                last_packet_ms = now_ms

        if time.ticks_diff(now_ms, last_packet_ms) >= idle_ms:
            break

        time.sleep_ms(1)

    for uid_text, dev in idx.items():
        if uid_text not in discovered_uids:
            dev["module_online"] = False
            dev["module_lost_epoch"] = time.time()

    db["devices"] = sorted(idx.values(), key=lambda d: d.get("unique_id", ""))
    save_device_db(db)
    return db["devices"]


def request_continuity_for_uid(uid_int, cue_count_hint=16, timeout_ms=300):
    drain_uart()
    radio_write_nfcp(nfcp_tx.build_continuity_request(uid_int))

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_CONTINUITY_RSP:
                continue
            if (pkt["src_unique"] & 0xFFFFFFFF) != (uid_int & 0xFFFFFFFF):
                continue
            update_radio_link_from_packet(pkt)
            return parse_continuity_rsp(pkt["data"], cue_count_hint)

        time.sleep_ms(2)

    return None


def request_ping_for_uid(uid_int, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    drain_uart()

    nonce = random.getrandbits(16)
    payload = (
        (uid_int & 0xFFFFFFFF).to_bytes(4, "big") +
        (nonce & 0xFFFF).to_bytes(2, "big")
    )
    start = time.ticks_ms()

    # Speed tests are unique-addressed only. The target UID is also echoed in
    # the payload so stale or unrelated ping responses cannot be accepted.
    radio_write_nfcp(nfcp_tx.build_ping(uid_int, payload))

    deadline = time.ticks_add(start, timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_PING:
                continue
            if (pkt["src_unique"] & 0xFFFFFFFF) != (uid_int & 0xFFFFFFFF):
                continue
            if pkt.get("data", b"") != payload:
                continue

            update_radio_link_from_packet(pkt)
            return time.ticks_diff(time.ticks_ms(), start)

        time.sleep_ms(2)

    return None


def request_info_for_uid(uid_int, timeout_ms=1000):
    drain_uart()
    radio_write_nfcp(nfcp_tx.build_info_request(uid_int))

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_INFO_RSP:
                continue
            if (pkt["src_unique"] & 0xFFFFFFFF) != (uid_int & 0xFFFFFFFF):
                continue

            update_radio_link_from_packet(pkt)
            return parse_info_rsp(pkt.get("data", b""))

        time.sleep_ms(2)

    return None


def info_scan(timeout_ms=1000):
    db = load_device_db()
    changed = False
    results = []

    for dev in db.get("devices", []):
        uid_text = dev.get("unique_id", "0x00000000")
        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            continue

        info = request_info_for_uid(uid, timeout_ms)
        if info:
            dev["app_name"] = info.get("app_name", "")
            dev["app_version"] = info.get("app_version", "")
            dev["radio_firmware_version"] = info.get("radio_version", "")
            dev["hardware_unique_id"] = info.get("hardware_unique_id", "")
            dev["configured_unique_id"] = info.get("configured_unique_id", "")
            dev["info_last_epoch"] = time.time()
            dev["module_online"] = True
            dev["module_last_seen_epoch"] = time.time()

            link = get_last_radio_link(uid_text)
            if link:
                if link.get("radio_rssi", None) is not None:
                    dev["radio_rssi"] = link["radio_rssi"]
                if link.get("radio_snr", None) is not None:
                    dev["radio_snr"] = link["radio_snr"]
                dev["radio_last_epoch"] = link.get("radio_last_epoch", time.time())

            results.append({
                "unique_id": uid_text,
                "info": info
            })
            changed = True

    if changed:
        save_device_db(db)

    return results


def continuity_scan(timeout_ms=300):
    db = load_device_db()
    changed = False
    results = []

    for dev in db.get("devices", []):
        uid_text = dev.get("unique_id", "0x00000000")
        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            continue

        try:
            cue_count = int(dev.get("cue_count", DEFAULT_CUE_COUNT) or DEFAULT_CUE_COUNT)
        except:
            cue_count = DEFAULT_CUE_COUNT

        ok = request_continuity_for_uid(uid, cue_count, timeout_ms)
        if ok is None:
            dev["module_online"] = False
            dev["module_lost_epoch"] = time.time()
            results.append({
                "unique_id": uid_text,
                "cue_count": cue_count,
                "continuity": None
            })
            changed = True
            continue

        cues = []
        for i in range(cue_count):
            cues.append({
                "cue": i + 1,
                "ok": bool(ok[i]) if i < len(ok) else False
            })

        dev["continuity"] = {"cues": cues}
        dev["continuity_last_epoch"] = time.time()
        dev["module_online"] = True
        dev["module_last_seen_epoch"] = time.time()

        link = get_last_radio_link(uid_text)
        if link:
            if link.get("radio_rssi", None) is not None:
                dev["radio_rssi"] = link["radio_rssi"]
            if link.get("radio_snr", None) is not None:
                dev["radio_snr"] = link["radio_snr"]
            dev["radio_last_epoch"] = link.get("radio_last_epoch", time.time())

        results.append({
            "unique_id": uid_text,
            "cue_count": cue_count,
            "continuity": ok
        })
        changed = True

    if changed:
        save_device_db(db)

    return results


def speed_test_scan(timeout_ms=SPEED_TEST_TIMEOUT_MS):
    db = load_device_db()
    changed = False
    results = []

    for dev in db.get("devices", []):
        uid_text = dev.get("unique_id", "0x00000000")
        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            continue

        ping_ms = request_ping_for_uid(uid, timeout_ms)
        dev["speed_test_target_uid"] = uid_text
        dev["speed_test_ms"] = ping_ms
        dev["speed_test_ok"] = ping_ms is not None
        dev["speed_test_last_epoch"] = time.time()
        dev["module_online"] = ping_ms is not None
        if ping_ms is None:
            dev["module_lost_epoch"] = time.time()
        else:
            dev["module_last_seen_epoch"] = time.time()

        link = get_last_radio_link(uid_text)
        if link:
            if link.get("radio_rssi", None) is not None:
                dev["radio_rssi"] = link["radio_rssi"]
            if link.get("radio_snr", None) is not None:
                dev["radio_snr"] = link["radio_snr"]
            dev["radio_last_epoch"] = link.get("radio_last_epoch", time.time())

        results.append({
            "unique_id": uid_text,
            "ping_ms": ping_ms
        })
        changed = True

    if changed:
        save_device_db(db)

    return results


def arm():
    radio_write_nfcp(nfcp_tx.build_arm_broadcast())


def disarm():
    radio_write_nfcp(nfcp_tx.build_disarm_broadcast())


def arm_for_uid(uid_int, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    return send_with_ack(uid_int, nfcp.CMD_ARM, lambda: nfcp_tx.build_arm_unique(uid_int), timeout_ms)


def disarm_for_uid(uid_int, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    return send_with_ack(uid_int, nfcp.CMD_DISARM, lambda: nfcp_tx.build_disarm_unique(uid_int), timeout_ms)


def fire_unique(uid_int, cue_index_0based):
    if cue_index_0based < 0 or cue_index_0based >= MAX_CUE_COUNT:
        raise ValueError("cue index out of range")

    radio_write_nfcp(nfcp_tx.build_unique_fire(uid_int, 1 << cue_index_0based))


def fire_unique_mask(uid_int, cue_mask):
    if cue_mask <= 0:
        raise ValueError("cue mask is empty")

    if cue_mask >> MAX_CUE_COUNT:
        raise ValueError("cue mask out of range")

    radio_write_nfcp(nfcp_tx.build_unique_fire(uid_int, cue_mask))


def set_group(uid_int, new_group):
    radio_write_nfcp(nfcp_tx.build_set_group(uid_int, new_group))


def request_set_network_for_uid(uid_int, new_network, timeout_ms=800):
    return send_with_ack(
        uid_int,
        nfcp.CMD_SET_NETWORK,
        lambda: nfcp_tx.build_set_network(uid_int, new_network),
        timeout_ms
    )


def request_set_unique_for_uid(uid_int, new_unique, timeout_ms=1200):
    return send_with_ack(
        uid_int,
        nfcp.CMD_SET_UNIQUE,
        lambda: nfcp_tx.build_set_unique(uid_int, new_unique),
        timeout_ms
    )


def request_set_radio_channel_for_uid(uid_int, new_channel, timeout_ms=1200):
    return send_with_ack(
        uid_int,
        nfcp.CMD_SET_RADIO_CHANNEL,
        lambda: nfcp_tx.build_set_radio_channel(uid_int, new_channel),
        timeout_ms
    )


def wait_for_ack(uid_int, orig_cmd, timeout_ms=1000):
    ok, _code = wait_for_ack_detail(uid_int, orig_cmd, timeout_ms)
    return ok


def wait_for_ack_detail(uid_int, orig_cmd, timeout_ms=1000):
    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if (pkt["src_unique"] & 0xFFFFFFFF) != (uid_int & 0xFFFFFFFF):
                continue

            data = pkt.get("data", b"")
            if pkt["cmd"] == nfcp.CMD_ACK and len(data) >= 2:
                if data[0] == orig_cmd:
                    return data[1] == nfcp.ACK_OK, data[1]

            if pkt["cmd"] == nfcp.CMD_NACK and len(data) >= 2:
                if data[0] == orig_cmd:
                    return False, data[1]

        time.sleep_ms(2)

    return False, None


def send_with_ack_detail(
    uid_int,
    orig_cmd,
    build_packet,
    timeout_ms=1000,
    attempts=ACK_RETRY_ATTEMPTS,
    retry_delay_ms=ACK_RETRY_DELAY_MS
):
    last_code = None

    for attempt in range(attempts):
        drain_uart()
        radio_write_nfcp(build_packet())
        ok, code = wait_for_ack_detail(uid_int, orig_cmd, timeout_ms)
        if ok or code is not None:
            return ok, code

        last_code = code
        if attempt + 1 < attempts:
            time.sleep_ms(retry_delay_ms + attempt * retry_delay_ms)

    return False, last_code


def send_with_ack(uid_int, orig_cmd, build_packet, timeout_ms=1000, attempts=ACK_RETRY_ATTEMPTS):
    ok, _code = send_with_ack_detail(uid_int, orig_cmd, build_packet, timeout_ms, attempts)
    return ok


def checksum_file(path):
    checksum = 0
    size = 0
    with open(path, "rb") as f:
        while True:
            data = f.read(128)
            if not data:
                break
            size += len(data)
            for b in data:
                if not isinstance(b, int):
                    b = ord(b)
                checksum = (checksum + b) & 0xFFFFFFFF
    return size, checksum


def e77_update_send_file_for_uid(uid_int, source_path, target_path, transfer_id, progress_cb=None):
    size, checksum = checksum_file(source_path)

    ok, code = send_with_ack_detail(
        uid_int,
        nfcp.CMD_E77_UPDATE_BEGIN,
        lambda: nfcp_tx.build_e77_update_begin(
            uid_int,
            transfer_id,
            target_path,
            size,
            checksum
        ),
        3000,
        attempts=2
    )
    if not ok:
        print("E77 update begin failed:", ack_code_text(code))
        return False

    offset = 0
    try:
        with open(source_path, "rb") as f:
            while offset < size:
                chunk = f.read(E77_UPDATE_CHUNK_SIZE)
                if not chunk:
                    break

                ok, code = send_with_ack_detail(
                    uid_int,
                    nfcp.CMD_E77_UPDATE_WRITE,
                    lambda: nfcp_tx.build_e77_update_write(
                        uid_int,
                        transfer_id,
                        offset,
                        chunk
                    ),
                    3000,
                    attempts=3
                )

                if not ok:
                    radio_write_nfcp(nfcp_tx.build_e77_update_abort(uid_int, transfer_id))
                    print("E77 update write failed:", ack_code_text(code))
                    return False

                offset += len(chunk)
                if progress_cb:
                    progress_cb(offset, size)

        ok, code = send_with_ack_detail(
            uid_int,
            nfcp.CMD_E77_UPDATE_END,
            lambda: nfcp_tx.build_e77_update_end(uid_int, transfer_id),
            4000,
            attempts=2
        )
        if not ok:
            print("E77 update end failed:", ack_code_text(code))
        return ok
    except Exception as e:
        print("E77 update send failed:", e)
        try:
            radio_write_nfcp(nfcp_tx.build_e77_update_abort(uid_int, transfer_id))
        except:
            pass
        return False


def e77_update_reboot_for_uid(uid_int, timeout_ms=1000):
    return send_with_ack(
        uid_int,
        nfcp.CMD_E77_UPDATE_REBOOT,
        lambda: nfcp_tx.build_e77_update_reboot(uid_int),
        timeout_ms
    )


def script_clear_status_for_uid(uid_int, script_id, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    return send_with_ack_detail(
        uid_int,
        nfcp.CMD_SCRIPT_CLEAR,
        lambda: nfcp_tx.build_script_clear(uid_int, script_id),
        timeout_ms,
        attempts=3
    )


def script_clear_for_uid(uid_int, script_id, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    ok, _code = script_clear_status_for_uid(uid_int, script_id, timeout_ms)
    return ok


def script_load_for_uid(uid_int, script_id, entries, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    chunk_index = 0
    offset = 0

    while offset < len(entries):
        chunk = entries[offset:offset + SCRIPT_LOAD_CHUNK_ENTRIES]
        ok, _code = send_with_ack_detail(
            uid_int,
            nfcp.CMD_SCRIPT_LOAD,
            lambda: nfcp_tx.build_script_load(uid_int, script_id, chunk_index, chunk),
            timeout_ms,
            attempts=2
        )

        if not ok:
            return False

        offset += len(chunk)
        chunk_index += 1

    return True


def script_master_ticks_ms():
    return time.ticks_ms() & 0x3FFFFFFF


def script_time_sync_broadcast(master_ticks_ms=None):
    if master_ticks_ms is None:
        master_ticks_ms = script_master_ticks_ms()
    radio_write_nfcp(nfcp_tx.build_time_sync_broadcast(master_ticks_ms))


def script_send_time_sync(repeats=SCRIPT_TIME_SYNC_REPEATS):
    for _ in range(repeats):
        script_time_sync_broadcast()
        time.sleep_ms(SCRIPT_TIME_SYNC_GAP_MS)


def script_start_broadcast(script_id, start_delay_ms=SCRIPT_START_DELAY_MS, offset_ms=0, start_master_ms=None):
    for _ in range(SCRIPT_START_BROADCAST_REPEATS):
        radio_write_nfcp(nfcp_tx.build_script_start_broadcast(script_id, start_delay_ms, offset_ms, start_master_ms))
        time.sleep_ms(20)


def script_start_for_uid(uid_int, script_id, start_delay_ms=SCRIPT_START_DELAY_MS, offset_ms=0, timeout_ms=SPEED_TEST_TIMEOUT_MS, start_master_ms=None):
    return send_with_ack(
        uid_int,
        nfcp.CMD_SCRIPT_START,
        lambda: nfcp_tx.build_script_start(uid_int, script_id, start_delay_ms, offset_ms, start_master_ms),
        timeout_ms
    )


def script_start_status_for_uid(uid_int, script_id, start_delay_ms=SCRIPT_START_DELAY_MS, offset_ms=0, timeout_ms=SPEED_TEST_TIMEOUT_MS, start_master_ms=None):
    return send_with_ack_detail(
        uid_int,
        nfcp.CMD_SCRIPT_START,
        lambda: nfcp_tx.build_script_start(uid_int, script_id, start_delay_ms, offset_ms, start_master_ms),
        timeout_ms,
        attempts=2
    )


def script_stop_broadcast(script_id=0):
    radio_write_nfcp(nfcp_tx.build_script_stop_broadcast(script_id))


def script_stop_for_uid(uid_int, script_id=0, timeout_ms=SPEED_TEST_TIMEOUT_MS):
    return send_with_ack(
        uid_int,
        nfcp.CMD_SCRIPT_STOP,
        lambda: nfcp_tx.build_script_stop(uid_int, script_id),
        timeout_ms
    )


def script_heartbeat_broadcast(script_id, elapsed_ms=0):
    radio_write_nfcp(nfcp_tx.build_script_heartbeat_broadcast(script_id, elapsed_ms))


def script_heartbeat_for_uid(uid_int, script_id, elapsed_ms=0):
    radio_write_nfcp(nfcp_tx.build_script_heartbeat(uid_int, script_id, elapsed_ms))


def script_step_for_uid(uid_int, script_id, event_ms):
    radio_write_nfcp(nfcp_tx.build_script_step(uid_int, script_id, event_ms))


nfcp.load_device_db = load_device_db
nfcp.save_device_db = save_device_db
nfcp.clear_device_db = clear_device_db
nfcp.index_db_by_unique = index_db_by_unique
nfcp.parse_discover_rsp = parse_discover_rsp
nfcp.parse_continuity_rsp = parse_continuity_rsp
nfcp.poll_responses = poll_responses
nfcp.get_last_packets = get_last_packets
nfcp.get_last_radio_link = get_last_radio_link
nfcp.discover = discover
nfcp.request_continuity_for_uid = request_continuity_for_uid
nfcp.continuity_scan = continuity_scan
nfcp.request_ping_for_uid = request_ping_for_uid
nfcp.speed_test_scan = speed_test_scan
nfcp.request_info_for_uid = request_info_for_uid
nfcp.info_scan = info_scan
nfcp.arm = arm
nfcp.disarm = disarm
nfcp.arm_for_uid = arm_for_uid
nfcp.disarm_for_uid = disarm_for_uid
nfcp.fire_unique = fire_unique
nfcp.fire_unique_mask = fire_unique_mask
nfcp.set_group = set_group
nfcp.request_set_network_for_uid = request_set_network_for_uid
nfcp.request_set_unique_for_uid = request_set_unique_for_uid
nfcp.request_set_radio_channel_for_uid = request_set_radio_channel_for_uid
nfcp.e77_update_send_file_for_uid = e77_update_send_file_for_uid
nfcp.e77_update_reboot_for_uid = e77_update_reboot_for_uid
nfcp.script_clear_status_for_uid = script_clear_status_for_uid
nfcp.script_clear_for_uid = script_clear_for_uid
nfcp.script_load_for_uid = script_load_for_uid
nfcp.script_start_broadcast = script_start_broadcast
nfcp.script_start_for_uid = script_start_for_uid
nfcp.script_start_status_for_uid = script_start_status_for_uid
nfcp.script_send_time_sync = script_send_time_sync
nfcp.script_stop_broadcast = script_stop_broadcast
nfcp.script_stop_for_uid = script_stop_for_uid
nfcp.script_heartbeat_broadcast = script_heartbeat_broadcast
nfcp.script_heartbeat_for_uid = script_heartbeat_for_uid
nfcp.script_step_for_uid = script_step_for_uid


class NFCPGui:
    def __init__(self):
        self.modules = []
        self.selected_module = None
        self.last_selected_module_uid = None
        self.selected_script = None

        self.clear_loaded_script()

        self.continuity = [False] * DEFAULT_CUE_COUNT
        self.armed = False
        self.current_screen = None
        self.current_view = "main"
        self.discover_busy = False
        self.modules_page = 0
        self.manual_fire_modules = []
        self.manual_fire_index = 0
        self.manual_fire_last_poll_ms = time.ticks_ms()
        self.manual_fire_last_fire_ms = time.ticks_ms()
        self.manual_last_fire_uid = None
        self.manual_last_fire_key = None
        self.manual_last_fire_blink_on = False
        self.manual_last_fire_blink_ms = time.ticks_ms()
        self.script_fire_mode = "auto"
        self.script_fire_running = False
        self.script_fire_elapsed_ms = 0
        self.script_fire_started_ms = time.ticks_ms()
        self.script_fire_last_clock_ms = time.ticks_ms()
        self.script_fire_step_index = 0
        self.script_fire_auto_index = 0
        self.script_fire_last_trellis_poll_ms = time.ticks_ms()
        self.script_fire_trigger_held = False
        self.script_fire_last_trigger_ms = time.ticks_ms()
        self.last_lvgl_handler_ms = time.ticks_ms()
        self.script_fire_prepared = False
        self.script_fire_auto_fired = 0
        self.script_fire_auto_packets = 0
        self.script_fire_step_status_pending = None
        self.script_fire_last_step_status_ms = time.ticks_ms()
        self.script_fire_module_auto_active = False
        self.script_fire_uploaded_id = None
        self.script_fire_uploaded_uids = []
        self.script_fire_upload_results = []
        self.script_fire_upload_warning = False
        self.script_fire_last_heartbeat_ms = time.ticks_ms()
        self.last_module_continuity_auto_ms = time.ticks_ms()
        self.settings = load_app_settings()
        self.auto_module_continuity = bool(
            self.settings.get("auto_module_continuity", True)
        )
        self.color_cues_without_script = bool(
            self.settings.get("color_cues_without_script", True)
        )
        self.radio_network_id = int(
            self.settings.get("radio_network_id", RADIO_NETWORK_ID)
        ) & 0xFFFF
        self.radio_channel = int(
            self.settings.get("radio_channel", RADIO_CHANNEL)
        )
        self.channel_scan_results = []
        self.radio_error = None
        self.trellis = None
        self.trellis_error = None

        self.group_edit_value = 1
        self.group_edit_text = ""

        self.trellis, self.trellis_error = init_trellis_boot_check()
        if self.trellis_error:
            print(self.trellis_error)
            self.show_trellis_error()
            return

        if not check_radio_ready(self.radio_network_id, self.radio_channel):
            self.radio_error = "E77 radio not responding"
            print(self.radio_error)
            self.show_radio_error()
            return

        self.load_modules_from_db()
        self.show_main_menu()

    def clear_loaded_script(self, script_name=None):
        self.loaded_script_type = None
        self.loaded_script_name = script_name
        self.loaded_script_firmware = None
        self.loaded_script_trigger_channel = None
        self.loaded_script_trigger_button = None
        self.loaded_script_audio = None

        self.loaded_script_event_count = 0
        self.loaded_script_fire_count = 0
        self.loaded_script_dmx_count = 0

        self.loaded_script_channels = []
        self.loaded_script_channel_count = 0
        self.loaded_script_mismatch = []
        self.loaded_script_extra_channels = []
        self.loaded_script_duplicate_channels = []

        self.loaded_script_pyro_events = []
        self.loaded_script_steps = []
        self.open_script_cue_count = 0
        self.extra_connected_cue_count = 0
        self.script_fire_step_index = 0
        self.script_fire_auto_index = 0
        self.script_fire_prepared = False
        self.script_fire_auto_fired = 0
        self.script_fire_auto_packets = 0
        self.script_fire_step_status_pending = None
        self.script_fire_module_auto_active = False
        self.script_fire_uploaded_id = None
        self.script_fire_uploaded_uids = []
        self.script_fire_upload_results = []
        self.script_fire_upload_warning = False
        self.script_fire_last_heartbeat_ms = time.ticks_ms()

    def update_background(self):
        color = lv.color_hex(0x400000) if self.armed else lv.color_hex(0x101010)

        if self.current_screen:
            self.current_screen.set_style_bg_color(color, 0)

    def delete_old_screen(self, old_screen):
        if old_screen is None:
            return

        try:
            if hasattr(old_screen, "delete_async"):
                old_screen.delete_async()
            elif hasattr(old_screen, "del_async"):
                old_screen.del_async()
            elif hasattr(old_screen, "delete"):
                old_screen.delete()
            elif hasattr(old_screen, "del"):
                getattr(old_screen, "del")()
        except Exception as e:
            print("Old screen delete failed:", e)

    def clear_screen_refs(self):
        names = (
            "status",
            "manual_fire_btn",
            "script_fire_btn",
            "arm_btn",
            "arm_btn_label",
            "discover_btn",
            "refresh_btn",
            "module_list",
            "module_label",
            "arm_label",
            "speed_label",
            "radio_link_label",
            "version_label",
            "summary",
            "grid_box",
            "cue_buttons",
            "cue_labels",
            "auto_continuity_checkbox",
            "no_script_color_checkbox",
            "loading_label",
            "group_edit_box",
            "group_edit_label",
            "network_edit_box",
            "network_edit_label",
            "manual_channel_label",
            "manual_module_label",
            "manual_summary_label",
            "manual_cue_buttons",
            "manual_cue_labels",
            "script_name_label",
            "script_clock_label",
            "script_status_label",
            "script_auto_btn",
            "script_step_btn",
        )

        for name in names:
            try:
                if hasattr(self, name):
                    delattr(self, name)
            except Exception:
                pass

    def new_screen(self, title_text, view_name=None):
        old_screen = self.current_screen
        old_view = self.current_view
        self.clear_screen_refs()
        scr = lv.obj()

        scr.set_style_bg_color(
            lv.color_hex(0x400000) if self.armed else lv.color_hex(0x101010),
            0
        )

        scr.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        load_screen(scr)
        self.delete_old_screen(old_screen)

        if old_view in ("manual_fire", "script_fire") and view_name != old_view:
            self.clear_trellis_leds()

        self.status = lv.label(scr)
        self.status.set_text("Status: Ready")
        self.status.align(lv.ALIGN.TOP_LEFT, 20, 15)

        title = lv.label(scr)
        title.set_text(title_text)
        title.align(lv.ALIGN.TOP_MID, 0, 10)

        self.current_screen = scr

        if view_name:
            self.current_view = view_name

        return scr

    def make_button(self, parent, text, x, y, w, h, cb):
        btn = lv.button(parent)
        btn.set_pos(x, y)
        btn.set_size(w, h)
        btn.add_event_cb(cb, lv.EVENT.CLICKED, None)

        lbl = lv.label(btn)
        lbl.set_text(text)
        lbl.center()

        return btn

    def apply_largest_font(self, obj):
        for name in (
            "font_montserrat_48",
            "font_montserrat_46",
            "font_montserrat_44",
            "font_montserrat_42",
            "font_montserrat_40",
            "font_montserrat_38",
            "font_montserrat_36",
        ):
            try:
                obj.set_style_text_font(getattr(lv, name), 0)
                return
            except:
                pass

    def show_radio_error(self):
        scr = self.new_screen("Radio Error", "radio_error")

        label = lv.label(scr)
        label.set_text(
            "E77 radio is not responding.\n"
            "Check radio power, UART wiring, and firmware."
        )
        label.align(lv.ALIGN.CENTER, 0, -20)

        retry = self.make_button(scr, "RETRY", 250, 310, 300, 70, self.retry_radio_boot_check)
        retry.set_style_bg_color(lv.color_hex(0x2F4F8F), 0)

    def retry_radio_boot_check(self, event=None):
        if check_radio_ready(self.radio_network_id):
            self.radio_error = None
            self.load_modules_from_db()
            self.show_main_menu()
            return

        self.radio_error = "E77 radio not responding"
        self.show_radio_error()

    def show_trellis_error(self):
        scr = self.new_screen("Trellis Error", "trellis_error")

        label = lv.label(scr)
        label.set_text(
            "Trellis board failed boot check.\n"
            "%s\n"
            "Check I2C wiring, address, power, and stuck buttons." %
            (self.trellis_error or "Unknown Trellis error")
        )
        label.align(lv.ALIGN.CENTER, 0, -35)

        retry = self.make_button(scr, "RETRY", 250, 310, 300, 70, self.retry_trellis_boot_check)
        retry.set_style_bg_color(lv.color_hex(0x2F4F8F), 0)

    def retry_trellis_boot_check(self, event=None):
        self.trellis, self.trellis_error = init_trellis_boot_check()
        if self.trellis_error:
            print(self.trellis_error)
            self.show_trellis_error()
            return

        if not check_radio_ready(self.radio_network_id):
            self.radio_error = "E77 radio not responding"
            print(self.radio_error)
            self.show_radio_error()
            return

        self.load_modules_from_db()
        self.show_main_menu()

    def group_to_int_display(self, group_value):
        try:
            if isinstance(group_value, str):
                return str(int(group_value, 16))
            return str(int(group_value))
        except:
            return str(group_value)

    def group_to_int_value(self, group_value, fallback=999999):
        try:
            if isinstance(group_value, str):
                return int(group_value, 16)
            return int(group_value)
        except:
            return fallback

    def parse_group_id(self, group_value, fallback=None):
        try:
            if isinstance(group_value, str):
                return int(group_value, 16)
            return int(group_value)
        except:
            return fallback

    def get_module_cue_count(self, module):
        try:
            cue_count = int(module.get("cue_count", DEFAULT_CUE_COUNT))
        except:
            cue_count = DEFAULT_CUE_COUNT

        if cue_count < 1:
            return DEFAULT_CUE_COUNT

        if cue_count > MAX_CUE_COUNT:
            return MAX_CUE_COUNT

        return cue_count

    def format_uid(self, uid_text):
        try:
            return "%08X" % (int(uid_text, 16) & 0xFFFFFFFF)
        except:
            return str(uid_text)

    def format_speed_test(self, module):
        if not module.get("speed_test_ok", False):
            return "--"

        try:
            return "%dms" % int(module.get("speed_test_ms", 0))
        except:
            return "--"

    def format_selected_speed_test(self):
        if not self.selected_module:
            return "Speed: --"

        return "Speed: %s" % self.format_speed_test(self.selected_module)

    def module_is_lost(self, module):
        return module.get("module_online", True) is False

    def format_rssi(self, module):
        try:
            rssi = module.get("radio_rssi", None)
        except:
            rssi = None

        if rssi is None:
            return "RSSI: --"

        try:
            return "RSSI: %ddBm" % int(rssi)
        except:
            return "RSSI: --"

    def format_snr(self, module):
        try:
            snr = module.get("radio_snr", None)
        except:
            snr = None

        if snr is None:
            return "SNR: --"

        try:
            return "SNR: %ddB" % int(snr)
        except:
            return "SNR: --"

    def format_selected_radio_link(self):
        if not self.selected_module:
            return "RSSI: --   SNR: --"

        return "%s   %s" % (
            self.format_rssi(self.selected_module),
            self.format_snr(self.selected_module)
        )

    def format_selected_versions(self):
        if not self.selected_module:
            return "FW: --   Radio: --"

        app_name = self.selected_module.get("app_name", "")
        app_version = self.selected_module.get("app_version", "")
        radio_version = self.selected_module.get("radio_firmware_version", "")

        if app_name and app_version:
            fw_text = "%s %s" % (app_name, app_version)
        elif app_version:
            fw_text = app_version
        else:
            fw_text = "--"

        if not radio_version:
            radio_version = "--"

        script_status = self.selected_module.get("script_status", "")
        if script_status:
            return "FW: %s   Radio: %s   Script: %s" % (
                fw_text,
                radio_version,
                script_status
            )

        return "FW: %s   Radio: %s" % (fw_text, radio_version)

    def format_selected_identity(self):
        if not self.selected_module:
            return "HW: --   OVR: --"

        hardware_uid = self.selected_module.get("hardware_unique_id", "")
        configured_uid = self.selected_module.get("configured_unique_id", "")

        if not hardware_uid:
            return "HW: --   OVR: --"

        if configured_uid in ("", "0x00000000", "0"):
            configured_uid = "auto"

        return "HW: %s   OVR: %s" % (hardware_uid, configured_uid)

    def format_module_continuity_summary(self, module):
        cue_count = self.get_module_cue_count(module)
        continuity = module.get("continuity", None)

        if not continuity:
            return "--/%d" % cue_count

        try:
            good = 0
            for cue_info in continuity.get("cues", []):
                if bool(cue_info.get("ok")):
                    good += 1

            return "%d/%d" % (good, cue_count)
        except:
            return "--/%d" % cue_count

    def set_modules_page_for_uid(self, uid_text):
        if not uid_text:
            return

        for i, module in enumerate(self.modules):
            if module.get("unique_id", None) == uid_text:
                self.modules_page = i // 9
                return

    def get_selected_module_cue_count(self):
        if not self.selected_module:
            return DEFAULT_CUE_COUNT

        return self.get_module_cue_count(self.selected_module)

    def get_module_saved_continuity(self, module):
        cue_count = self.get_module_cue_count(module)
        out = [False] * cue_count
        continuity = module.get("continuity", None)

        if not continuity:
            return out

        try:
            for cue_info in continuity.get("cues", []):
                cue_num = int(cue_info.get("cue", 0))
                ok = bool(cue_info.get("ok"))

                if 1 <= cue_num <= cue_count:
                    out[cue_num - 1] = ok
        except Exception as e:
            print("Saved continuity parse failed:", e)

        return out

    def module_matches_selected(self, module):
        if not self.selected_module:
            return False

        return (
            module.get("unique_id", None) ==
            self.selected_module.get("unique_id", None)
        )

    def module_has_continuity_data(self, module):
        if self.module_matches_selected(module):
            if self.selected_module.get("continuity", None):
                return True

        return module.get("continuity", None) is not None

    def get_module_continuity_list(self, module):
        cue_count = self.get_module_cue_count(module)

        if self.module_matches_selected(module):
            out = [False] * cue_count
            for i in range(cue_count):
                out[i] = bool(self.continuity[i]) if i < len(self.continuity) else False
            return out

        return self.get_module_saved_continuity(module)

    def ensure_bridge_channel(self):
        ok, message = ensure_local_radio_channel(self.radio_channel)

        if message:
            self.set_status(message)

        if not ok:
            self.radio_error = message or "Bridge channel check failed"
            return False

        self.radio_error = None
        return True

    def update_modules_busy_buttons(self):
        if hasattr(self, "discover_btn"):
            if self.discover_busy:
                self.discover_btn.add_state(lv.STATE.DISABLED)
            else:
                self.discover_btn.remove_state(lv.STATE.DISABLED)

        if hasattr(self, "refresh_btn"):
            if self.discover_busy:
                self.refresh_btn.add_state(lv.STATE.DISABLED)
            else:
                self.refresh_btn.remove_state(lv.STATE.DISABLED)

        self.update_arm_button()

    # -------------------------
    # Main menu
    # -------------------------

    def show_main_menu(self, event=None):
        scr = self.new_screen("NFCP Master Controller", "main")

        self.make_button(scr, "MODULES", 60, 90, 300, 80, self.show_modules_menu)
        self.make_button(scr, "LOAD SCRIPT", 440, 90, 300, 80, self.show_load_script)

        self.script_fire_btn = self.make_button(
            scr,
            "SCRIPT FIRE",
            60,
            200,
            300,
            80,
            self.show_script_fire
        )
        self.make_button(scr, "SETTINGS", 440, 200, 300, 80, self.show_settings)

        self.manual_fire_btn = self.make_button(
            scr,
            "MANUAL FIRE",
            60,
            315,
            300,
            80,
            self.show_manual_fire
        )

        self.arm_btn = lv.button(scr)
        self.arm_btn.set_pos(440, 315)
        self.arm_btn.set_size(300, 80)
        self.arm_btn.add_event_cb(self.toggle_arm, lv.EVENT.CLICKED, None)

        self.arm_btn_label = lv.label(self.arm_btn)
        self.arm_btn_label.set_text("DISARM" if self.armed else "ARM")
        self.arm_btn_label.center()

        self.update_manual_fire_button()
        self.update_script_fire_button()
        self.update_arm_button()

    def update_manual_fire_button(self):
        if not hasattr(self, "manual_fire_btn"):
            return

        try:
            if self.armed:
                self.manual_fire_btn.remove_state(lv.STATE.DISABLED)
            else:
                self.manual_fire_btn.add_state(lv.STATE.DISABLED)
        except Exception as e:
            print("Manual fire button state update failed:", e)

    def update_script_fire_button(self):
        if not hasattr(self, "script_fire_btn"):
            return

        try:
            if self.armed and self.selected_script:
                self.script_fire_btn.remove_state(lv.STATE.DISABLED)
            else:
                self.script_fire_btn.add_state(lv.STATE.DISABLED)
        except Exception as e:
            print("Script fire button state update failed:", e)

    def show_script_fire(self, event=None):
        if not self.selected_script:
            self.set_status("Script fire disabled - no script loaded")
            return

        if not self.armed:
            self.set_status("Script fire disabled - not armed")
            return

        if not self.script_fire_running:
            self.script_fire_elapsed_ms = 0
            self.script_fire_step_index = 0
            self.script_fire_auto_index = 0
            self.preload_script_fire_modules()

        self.show_script_fire_page()

    def show_script_fire_page(self):
        if not self.selected_script:
            self.set_status("Script fire disabled - no script loaded")
            return

        if not self.armed:
            self.set_status("Script fire disabled - not armed")
            return

        scr = self.new_screen("Script Fire", "script_fire")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)

        self.script_name_label = lv.label(scr)
        self.script_name_label.set_text("Script: %s" % (self.loaded_script_name or self.selected_script))
        self.script_name_label.set_pos(35, 58)

        self.script_stats_label = lv.label(scr)
        self.script_stats_label.set_text(
            "Events: %d   Pyro: %d   Open: %d/%d +%d" %
            (
                self.loaded_script_event_count,
                self.loaded_script_fire_count,
                self.open_script_cue_count,
                self.loaded_script_fire_count,
                self.extra_connected_cue_count
            )
        )
        self.script_stats_label.set_pos(35, 82)

        self.script_upload_label = lv.label(scr)
        self.script_upload_label.set_text(self.format_script_upload_summary())
        self.script_upload_label.set_pos(35, 106)

        self.script_clock_label = lv.label(scr)
        self.script_clock_label.set_text(self.format_script_fire_time())
        clock_font = get_script_clock_font()
        if clock_font:
            self.script_clock_label.set_style_text_font(clock_font, 0)
        else:
            self.apply_largest_font(self.script_clock_label)
            try:
                self.script_clock_label.set_style_transform_zoom(420, 0)
            except:
                pass
        self.script_clock_label.align(lv.ALIGN.CENTER, -70, -40)

        self.script_status_label = lv.label(scr)
        if self.script_fire_running:
            status_text = "Playing"
        elif self.script_fire_elapsed_ms > 0:
            status_text = "Paused"
        else:
            status_text = "Stopped"
        self.script_status_label.set_text(status_text)
        self.script_status_label.align(lv.ALIGN.CENTER, -70, 40)

        self.script_play_btn = self.make_button(scr, "PLAY", 150, 325, 140, 65, self.script_fire_play)
        self.script_pause_btn = self.make_button(scr, "PAUSE", 330, 325, 140, 65, self.script_fire_pause)
        self.script_stop_btn = self.make_button(scr, "STOP", 510, 325, 140, 65, self.script_fire_stop)

        self.script_auto_btn = self.make_button(
            scr,
            "AUTO",
            660,
            45,
            120,
            170,
            self.select_script_fire_auto
        )

        self.script_step_btn = self.make_button(
            scr,
            "STEP",
            660,
            220,
            120,
            170,
            self.select_script_fire_step
        )

        self.update_script_fire_mode_buttons()
        self.update_script_fire_transport_buttons()
        self.reset_script_fire_trigger_state()
        if not self.script_fire_prepared:
            try:
                self.prepare_script_fire_packets()
            except Exception as e:
                print("Script fire packet prep failed:", e)
        self.update_script_fire_clock(force=True)
        if self.script_fire_mode == "step":
            self.update_script_fire_step_status()
        elif self.script_fire_upload_warning and not self.script_fire_running:
            self.set_script_fire_status_label("Loaded with warnings")

    def format_script_fire_time(self):
        elapsed = self.script_fire_elapsed_ms

        if self.script_fire_running:
            elapsed = time.ticks_diff(time.ticks_ms(), self.script_fire_started_ms)

        if elapsed < 0:
            elapsed = 0

        minutes = elapsed // 60000
        seconds = (elapsed // 1000) % 60
        tenths = (elapsed // 100) % 10

        return "%02d:%02d.%d" % (minutes, seconds, tenths)

    def update_script_fire_clock(self, force=False):
        if not hasattr(self, "script_clock_label"):
            return

        now = time.ticks_ms()
        min_interval = 50 if self.script_fire_running else 100
        if not force and time.ticks_diff(now, self.script_fire_last_clock_ms) < min_interval:
            return

        self.script_fire_last_clock_ms = now
        self.script_clock_label.set_text(self.format_script_fire_time())
        self.script_clock_label.align(lv.ALIGN.CENTER, -70, -40)

    def set_script_fire_status_label(self, text):
        if not hasattr(self, "script_status_label"):
            return

        self.script_status_label.set_text(text)
        self.script_status_label.align(lv.ALIGN.CENTER, -70, 40)

    def format_script_upload_summary(self):
        if not self.script_fire_upload_results:
            return "Upload: Not loaded"

        ok_count = 0
        fail_count = 0
        fail_uid = None

        for item in self.script_fire_upload_results:
            if item.get("ok", False):
                ok_count += 1
            else:
                fail_count += 1
                if fail_uid is None:
                    fail_uid = item.get("uid", 0)

        if fail_count:
            return "Upload: %d loaded, %d failed   First: %08X" % (
                ok_count,
                fail_count,
                fail_uid & 0xFFFFFFFF
            )

        return "Upload: %d module(s) loaded" % ok_count

    def update_script_fire_transport_buttons(self):
        if not hasattr(self, "script_play_btn"):
            return

        try:
            can_play = (
                (not self.script_fire_running) and
                bool(self.loaded_script_steps)
            )

            if self.script_fire_mode == "auto":
                can_play = can_play and bool(self.script_fire_uploaded_uids)

            if can_play:
                self.script_play_btn.remove_state(lv.STATE.DISABLED)
            else:
                self.script_play_btn.add_state(lv.STATE.DISABLED)
        except Exception as e:
            print("Script play button update failed:", e)

    def reset_script_fire_trigger_state(self):
        now = time.ticks_ms()
        self.script_fire_trigger_held = False
        self.script_fire_last_trellis_poll_ms = now
        self.script_fire_last_trigger_ms = now

    def parse_script_time_ms(self, text):
        try:
            text = str(text).strip()
            if text.endswith("s") or text.endswith("S"):
                text = text[:-1]

            parts = text.split(":")
            if len(parts) == 3:
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds_text = parts[2]
            elif len(parts) == 2:
                hours = 0
                minutes = int(parts[0])
                seconds_text = parts[1]
            else:
                hours = 0
                minutes = 0
                seconds_text = parts[0]

            if "." in seconds_text:
                seconds_part, frac_part = seconds_text.split(".", 1)
            else:
                seconds_part = seconds_text
                frac_part = ""

            seconds = int(seconds_part)
            frac_part = frac_part[:3]
            while len(frac_part) < 3:
                frac_part += "0"

            millis = int(frac_part) if frac_part else 0

            return (
                (hours * 3600000) +
                (minutes * 60000) +
                (seconds * 1000) +
                millis
            )
        except Exception as e:
            print("Script time parse failed:", text, e)
            return 0

    def build_script_fire_steps(self):
        self.loaded_script_steps = []
        steps_by_time = {}

        for event in self.loaded_script_pyro_events:
            event_time = event.get("event_time", "")

            if event_time in steps_by_time:
                steps_by_time[event_time]["events"].append(event)
            else:
                step = {
                    "event_time": event_time,
                    "event_ms": self.parse_script_time_ms(event_time),
                    "events": [event],
                    "packets": [],
                    "fired_cues": 0,
                    "packet_count": 0
                }
                steps_by_time[event_time] = step
                self.loaded_script_steps.append(step)

        self.loaded_script_steps.sort(key=lambda step: step.get("event_ms", 0))

        if self.script_fire_step_index >= len(self.loaded_script_steps):
            self.script_fire_step_index = 0
        if self.script_fire_auto_index >= len(self.loaded_script_steps):
            self.script_fire_auto_index = 0
        self.script_fire_prepared = False
        self.script_fire_uploaded_id = None
        self.script_fire_uploaded_uids = []
        self.script_fire_upload_results = []
        self.script_fire_upload_warning = False

    def get_script_trigger_key(self):
        try:
            button = int(str(self.loaded_script_trigger_button).strip())
        except:
            return None

        if button < 1 or button > 16:
            return None

        return button - 1

    def update_script_fire_step_led(self):
        if self.current_view != "script_fire":
            return
        if self.script_fire_mode != "step":
            self.clear_trellis_leds()
            return
        if not self.trellis:
            return

        trigger_key = self.get_script_trigger_key()
        if trigger_key is None:
            self.clear_trellis_leds()
            return

        try:
            self.trellis.clear()
            self.trellis.set_led(trigger_key)
            self.trellis.write_display()
        except Exception as e:
            print("Script step LED update failed:", e)

    def update_script_fire_step_status(self, prefix=None):
        total = len(self.loaded_script_steps)
        if total:
            text = "Step %d / %d" % (self.script_fire_step_index + 1, total)
            if prefix:
                text = prefix + "   " + text
        else:
            text = "No pyro steps"

        self.set_script_fire_status_label(text)

    def queue_script_fire_step_status(self, text):
        self.script_fire_step_status_pending = text

    def update_pending_script_fire_step_status(self):
        if self.current_view != "script_fire":
            self.script_fire_step_status_pending = None
            return
        if self.script_fire_mode != "step":
            self.script_fire_step_status_pending = None
            return
        if self.script_fire_step_status_pending is None:
            return

        now = time.ticks_ms()
        if time.ticks_diff(now, self.script_fire_last_step_status_ms) < 75:
            return

        self.script_fire_last_step_status_ms = now
        pending = self.script_fire_step_status_pending
        self.script_fire_step_status_pending = None
        self.update_script_fire_step_status(pending)

    def count_mask_bits(self, mask):
        count = 0

        while mask:
            if mask & 1:
                count += 1
            mask >>= 1

        return count

    def build_script_step_channel_masks(self, events):
        channel_masks = {}

        for event in events:
            try:
                channel = int(event.get("channel"))
                cue = int(event.get("cue"))
            except:
                continue

            if cue < 1 or cue > MAX_CUE_COUNT:
                continue

            channel_masks[channel] = channel_masks.get(channel, 0) | (1 << (cue - 1))

        return channel_masks

    def build_script_channel_uid_map(self):
        channel_uids = {}

        for module in self.modules:
            channel = self.parse_group_id(module.get("group_id", None))
            if channel is None:
                continue

            try:
                uid = int(module.get("unique_id", "0x00000000"), 16) & 0xFFFFFFFF
            except:
                continue

            if channel not in channel_uids:
                channel_uids[channel] = []

            channel_uids[channel].append(uid)

        return channel_uids

    def prepare_script_fire_packets(self):
        self.load_modules_from_db()
        channel_uids = self.build_script_channel_uid_map()

        for step in self.loaded_script_steps:
            packets = []
            fired_cues = 0
            step_uids = []
            channel_masks = self.build_script_step_channel_masks(
                step.get("events", [])
            )

            for channel, cue_mask in channel_masks.items():
                uids = channel_uids.get(channel, [])
                if uids:
                    fired_cues += self.count_mask_bits(cue_mask)

                for uid in uids:
                    packets.append(nfcp_tx.build_unique_fire(uid, cue_mask))
                    if uid not in step_uids:
                        step_uids.append(uid)

            step["packets"] = packets
            step["script_step_uids"] = step_uids
            step["fired_cues"] = fired_cues
            step["packet_count"] = len(packets)

        self.script_fire_prepared = True

    def make_script_fire_id(self):
        try:
            value = random.getrandbits(16)
        except:
            value = time.ticks_ms() & 0xFFFF
        if value == 0:
            value = 1
        return value

    def build_module_script_entries(self):
        self.load_modules_from_db()
        channel_uids = self.build_script_channel_uid_map()
        uid_entries = {}

        if not self.loaded_script_steps:
            self.build_script_fire_steps()

        for step in self.loaded_script_steps:
            event_ms = int(step.get("event_ms", 0) or 0)
            channel_masks = self.build_script_step_channel_masks(
                step.get("events", [])
            )

            for channel, cue_mask in channel_masks.items():
                for uid in channel_uids.get(channel, []):
                    if uid not in uid_entries:
                        uid_entries[uid] = []
                    uid_entries[uid].append((event_ms, cue_mask & 0xFFFF))

        return uid_entries

    def preload_script_fire_modules(self):
        self.script_fire_module_auto_active = False
        self.script_fire_uploaded_id = None
        self.script_fire_uploaded_uids = []
        self.script_fire_upload_results = []
        self.script_fire_upload_warning = False

        self.show_loading_screen("Loading script...\nPreparing module schedules...")

        script_id = self.upload_script_to_modules()
        self.script_fire_upload_warning = self.script_fire_upload_has_failures()

        if script_id is None:
            self.set_status("Script preload failed")
            return

        if self.script_fire_upload_warning:
            self.set_status("Script loaded with module warnings")
        else:
            self.set_status("Script loaded to modules")

    def script_fire_upload_has_failures(self):
        if not self.script_fire_upload_results:
            return False

        for item in self.script_fire_upload_results:
            if not item.get("ok", False):
                return True

        return False

    def upload_script_to_modules(self):
        uid_entries = self.build_module_script_entries()
        self.script_fire_upload_results = []
        if not uid_entries:
            self.set_script_fire_status_label(
                "No module events: steps=%d modules=%d" %
                (
                    len(self.loaded_script_steps),
                    len(self.modules)
                )
            )
            self.set_status("No module script events to upload")
            return None

        script_id = self.make_script_fire_id()
        total_modules = len(uid_entries)
        total_entries = 0
        for entries in uid_entries.values():
            total_entries += len(entries)

        self.set_script_fire_status_label(
            "Upload ready: modules=%d entries=%d" %
            (
                total_modules,
                total_entries
            )
        )
        self.flush_ui()

        loaded = 0

        for uid, entries in uid_entries.items():
            loaded += 1
            try:
                uid_text = "0x%08X" % (uid & 0xFFFFFFFF)
            except:
                uid_text = str(uid)
            upload_text = "Upload %d/%d %s entries=%d" % (
                loaded,
                total_modules,
                uid_text,
                len(entries)
            )
            self.set_script_fire_status_label(upload_text)
            self.set_status(upload_text)
            self.flush_ui()

            clear_ok, clear_code = nfcp.script_clear_status_for_uid(uid, script_id)
            if not clear_ok:
                reason = ack_code_text(clear_code)
                self.script_fire_upload_results.append({
                    "uid": uid,
                    "entries": len(entries),
                    "ok": False,
                    "reason": "clear " + reason
                })
                self.set_script_fire_status_label(
                    "Upload clear failed %s: %s" %
                    (
                        uid_text,
                        reason
                    )
                )
                self.set_status("Script upload clear failed: " + reason)
                continue

            if not nfcp.script_load_for_uid(uid, script_id, entries):
                self.script_fire_upload_results.append({
                    "uid": uid,
                    "entries": len(entries),
                    "ok": False,
                    "reason": "load failed"
                })
                self.set_script_fire_status_label("Upload failed %s" % uid_text)
                self.set_status("Script upload failed")
                continue

            self.script_fire_upload_results.append({
                "uid": uid,
                "entries": len(entries),
                "ok": True,
                "reason": "loaded"
            })

        self.script_fire_uploaded_id = script_id
        self.script_fire_uploaded_uids = [
            item.get("uid", 0)
            for item in self.script_fire_upload_results
            if item.get("ok", False)
        ]

        if not self.script_fire_uploaded_uids:
            self.set_script_fire_status_label("No modules loaded")
            self.set_status("Script preload failed - no modules loaded")
            return None

        self.set_script_fire_status_label(
            "Uploaded id=%04X modules=%d/%d" %
            (
                script_id,
                len(self.script_fire_uploaded_uids),
                total_modules
            )
        )
        self.set_status(
            "Script uploaded to %d/%d module(s)" %
            (
                len(self.script_fire_uploaded_uids),
                total_modules
            )
        )
        return script_id

    def send_script_heartbeat(self, force=False):
        if not self.script_fire_module_auto_active:
            return
        if not self.script_fire_uploaded_id:
            return

        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self.script_fire_last_heartbeat_ms) < SCRIPT_HEARTBEAT_MS:
            return

        self.script_fire_last_heartbeat_ms = now
        try:
            elapsed_ms = self.get_script_fire_elapsed_ms()
            if self.script_fire_uploaded_uids:
                for uid in self.script_fire_uploaded_uids:
                    nfcp.script_heartbeat_for_uid(
                        uid,
                        self.script_fire_uploaded_id,
                        elapsed_ms
                    )
            else:
                nfcp.script_heartbeat_broadcast(
                    self.script_fire_uploaded_id,
                    elapsed_ms
                )
        except Exception as e:
            print("Script heartbeat failed:", e)

    def fire_prepared_script_step(self, step):
        packets_sent = 0

        if not self.script_fire_prepared:
            self.prepare_script_fire_packets()

        script_id = self.script_fire_uploaded_id
        step_uids = step.get("script_step_uids", [])
        if script_id is not None and step_uids:
            event_ms = int(step.get("event_ms", 0) or 0)
            for uid in step_uids:
                if uid not in self.script_fire_uploaded_uids:
                    continue
                try:
                    nfcp.script_step_for_uid(uid, script_id, event_ms)
                    packets_sent += 1
                except Exception as e:
                    print("Script step command failed:", e)

            if packets_sent:
                return step.get("fired_cues", 0), packets_sent

        for pkt in step.get("packets", []):
            try:
                radio_write_nfcp(pkt)
                packets_sent += 1
            except Exception as e:
                print("Script step fire mask failed:", e)

        return step.get("fired_cues", 0), packets_sent

    def advance_script_fire_step(self):
        if not self.armed:
            self.set_status("Script step blocked - not armed")
            return

        if not self.loaded_script_steps:
            self.set_status("No script steps loaded")
            self.update_script_fire_step_status()
            return

        if self.script_fire_step_index >= len(self.loaded_script_steps):
            self.script_fire_step_index = 0

        step = self.loaded_script_steps[self.script_fire_step_index]
        fired, _packets = self.fire_prepared_script_step(step)

        event_time = step.get("event_time", "--")
        self.script_fire_step_index += 1
        if self.script_fire_step_index >= len(self.loaded_script_steps):
            self.script_fire_step_index = 0

        self.queue_script_fire_step_status(
            "Fired %d cues @ %s" % (fired, event_time)
        )

    def get_script_fire_elapsed_ms(self):
        if self.script_fire_running:
            return time.ticks_diff(time.ticks_ms(), self.script_fire_started_ms)

        return self.script_fire_elapsed_ms

    def prepare_script_fire_auto_index(self):
        elapsed = self.get_script_fire_elapsed_ms()
        self.script_fire_auto_index = 0

        if not self.script_fire_prepared:
            self.prepare_script_fire_packets()

        while self.script_fire_auto_index < len(self.loaded_script_steps):
            step = self.loaded_script_steps[self.script_fire_auto_index]
            if step.get("event_ms", 0) >= elapsed:
                break
            self.script_fire_auto_index += 1

    def poll_script_fire_auto(self):
        if self.current_view != "script_fire":
            return
        if self.script_fire_mode != "auto":
            return
        if not self.script_fire_running:
            return

        if not self.loaded_script_steps:
            self.script_fire_running = False
            self.set_status("No script steps loaded")
            return

        elapsed = self.get_script_fire_elapsed_ms()
        if self.script_fire_module_auto_active:
            self.send_script_heartbeat()
            last_ms = self.loaded_script_steps[-1].get("event_ms", 0)
            if elapsed >= last_ms:
                self.script_fire_stop()
                self.set_status("Module auto complete")
            return

        total_fired = 0
        total_packets = 0

        if not self.script_fire_prepared:
            self.prepare_script_fire_packets()

        while self.script_fire_auto_index < len(self.loaded_script_steps):
            step = self.loaded_script_steps[self.script_fire_auto_index]
            if step.get("event_ms", 0) > elapsed:
                break

            fired, packets = self.fire_prepared_script_step(step)
            total_fired += fired
            total_packets += packets
            self.script_fire_auto_index += 1

        if total_fired:
            self.script_fire_auto_fired += total_fired
            self.script_fire_auto_packets += total_packets

        if self.script_fire_auto_index >= len(self.loaded_script_steps):
            self.script_fire_stop()
            self.set_status("Script fire auto complete")

    def poll_script_fire_trellis(self):
        if self.current_view != "script_fire":
            return
        if self.script_fire_mode != "step":
            return
        if not self.trellis:
            return

        trigger_key = self.get_script_trigger_key()
        if trigger_key is None:
            self.script_fire_trigger_held = False
            return

        now = time.ticks_ms()
        if time.ticks_diff(now, self.script_fire_last_trellis_poll_ms) < TRELLIS_POLL_MS:
            return

        self.script_fire_last_trellis_poll_ms = now

        try:
            self.trellis.read_switches()
            pressed = self.trellis.is_key_pressed(trigger_key)
            debounced = (
                time.ticks_diff(now, self.script_fire_last_trigger_ms) >=
                TRELLIS_STEP_DEBOUNCE_MS
            )
            if pressed and not self.script_fire_trigger_held and debounced:
                self.script_fire_last_trigger_ms = now
                self.advance_script_fire_step()
            self.script_fire_trigger_held = pressed
        except Exception as e:
            self.script_fire_trigger_held = False
            print("Script fire Trellis poll failed:", e)
            self.set_status("Script fire Trellis poll failed")

    def script_fire_play(self, event=None):
        if self.script_fire_running:
            return

        if self.script_fire_mode == "auto":
            self.script_fire_auto_fired = 0
            self.script_fire_auto_packets = 0
            if not self.loaded_script_steps:
                self.set_status("No script steps loaded")
                return

            script_id = self.script_fire_uploaded_id
            if script_id is None or not self.script_fire_uploaded_uids:
                self.set_status("Script not loaded to modules")
                return

            self.script_fire_module_auto_active = True
            self.script_fire_uploaded_id = script_id
            self.script_fire_last_heartbeat_ms = time.ticks_ms()
            nfcp.script_send_time_sync()
            start_master_ms = time.ticks_add(
                script_master_ticks_ms(),
                SCRIPT_START_DELAY_MS
            )
            time.sleep_ms(100)

            nfcp.script_start_broadcast(script_id, SCRIPT_START_DELAY_MS, self.script_fire_elapsed_ms, start_master_ms)
            self.script_fire_started_ms = time.ticks_add(
                time.ticks_ms(),
                SCRIPT_START_DELAY_MS - self.script_fire_elapsed_ms
            )
            self.script_fire_running = True
            self.send_script_heartbeat(force=True)
            self.update_script_fire_transport_buttons()
            self.set_script_fire_status_label(
                "Playing id=%04X heartbeat=%dms" %
                (
                    script_id,
                    SCRIPT_HEARTBEAT_MS
                )
            )
            self.set_status("Module auto playing")
            return
        elif not self.script_fire_prepared:
            self.prepare_script_fire_packets()

        self.script_fire_started_ms = time.ticks_add(
            time.ticks_ms(),
            -self.script_fire_elapsed_ms
        )
        self.script_fire_running = True
        self.update_script_fire_transport_buttons()

        self.set_script_fire_status_label("Playing")
        self.set_status("Script fire playing")

    def script_fire_pause(self, event=None):
        if self.script_fire_running:
            self.script_fire_elapsed_ms = time.ticks_diff(
                time.ticks_ms(),
                self.script_fire_started_ms
            )

        if self.script_fire_module_auto_active:
            try:
                if self.script_fire_uploaded_uids:
                    for uid in self.script_fire_uploaded_uids:
                        nfcp.script_stop_for_uid(uid, self.script_fire_uploaded_id or 0)
                else:
                    nfcp.script_stop_broadcast(self.script_fire_uploaded_id or 0)
            except Exception as e:
                print("Script pause stop broadcast failed:", e)
            self.script_fire_module_auto_active = False

        self.script_fire_running = False
        self.update_script_fire_clock(force=True)
        self.update_script_fire_transport_buttons()

        self.set_script_fire_status_label("Paused")
        self.set_status("Script fire paused")

    def script_fire_stop(self, event=None):
        if self.script_fire_module_auto_active:
            try:
                if self.script_fire_uploaded_uids:
                    for uid in self.script_fire_uploaded_uids:
                        nfcp.script_stop_for_uid(uid, self.script_fire_uploaded_id or 0)
                else:
                    nfcp.script_stop_broadcast(self.script_fire_uploaded_id or 0)
            except Exception as e:
                print("Script stop broadcast failed:", e)

        self.script_fire_running = False
        self.script_fire_module_auto_active = False
        self.script_fire_elapsed_ms = 0
        self.script_fire_step_index = 0
        self.script_fire_auto_index = 0
        self.update_script_fire_clock(force=True)
        self.update_script_fire_transport_buttons()

        self.set_script_fire_status_label("Stopped")
        self.set_status("Script fire stopped")

    def select_script_fire_auto(self, event=None):
        self.script_fire_mode = "auto"
        self.reset_script_fire_trigger_state()
        self.update_script_fire_mode_buttons()
        self.update_script_fire_transport_buttons()
        self.clear_trellis_leds()
        self.set_script_fire_status_label(
            "Playing" if self.script_fire_running else "Stopped"
        )
        self.set_status("Script fire mode: Auto")

    def select_script_fire_step(self, event=None):
        if self.script_fire_running:
            self.script_fire_elapsed_ms = self.get_script_fire_elapsed_ms()
            self.script_fire_running = False
            if self.script_fire_module_auto_active:
                try:
                    if self.script_fire_uploaded_uids:
                        for uid in self.script_fire_uploaded_uids:
                            nfcp.script_stop_for_uid(uid, self.script_fire_uploaded_id or 0)
                    else:
                        nfcp.script_stop_broadcast(self.script_fire_uploaded_id or 0)
                except Exception as e:
                    print("Script stop broadcast failed:", e)
                self.script_fire_module_auto_active = False

        self.script_fire_mode = "step"
        self.reset_script_fire_trigger_state()
        self.update_script_fire_mode_buttons()
        self.update_script_fire_transport_buttons()
        self.update_script_fire_step_led()
        self.update_script_fire_step_status()
        if self.get_script_trigger_key() is None:
            self.set_status("Script trigger button not set")
        else:
            self.set_status("Script fire mode: Step")

    def update_script_fire_mode_buttons(self):
        if not hasattr(self, "script_auto_btn") or not hasattr(self, "script_step_btn"):
            return

        selected_color = lv.color_hex(0x1F7A1F)
        normal_color = lv.color_hex(0x2F4F8F)

        if self.script_fire_mode == "auto":
            self.script_auto_btn.set_style_bg_color(selected_color, 0)
            self.script_auto_btn.add_state(lv.STATE.DISABLED)
            self.script_step_btn.set_style_bg_color(normal_color, 0)
            self.script_step_btn.remove_state(lv.STATE.DISABLED)
        else:
            self.script_step_btn.set_style_bg_color(selected_color, 0)
            self.script_step_btn.add_state(lv.STATE.DISABLED)
            self.script_auto_btn.set_style_bg_color(normal_color, 0)
            self.script_auto_btn.remove_state(lv.STATE.DISABLED)

        self.update_script_fire_step_led()

    def has_scanned_modules(self):
        return len(self.modules) > 0

    def get_module_uids(self):
        uids = []

        for module in self.modules:
            try:
                uid = int(module.get("unique_id", "0x00000000"), 16) & 0xFFFFFFFF
            except:
                continue
            if uid not in uids:
                uids.append(uid)

        return uids

    def update_arm_button(self):
        if not hasattr(self, "arm_btn"):
            return

        try:
            if self.has_scanned_modules() and not self.discover_busy:
                self.arm_btn.remove_state(lv.STATE.DISABLED)
            else:
                self.arm_btn.add_state(lv.STATE.DISABLED)
        except Exception as e:
            print("Arm button state update failed:", e)

    # -------------------------
    # View / Load Script
    # -------------------------

    def show_view_script(self, event=None):
        scr = self.new_screen("View Script", "view_script")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)

        if not self.selected_script:
            label = lv.label(scr)
            label.set_text("No script loaded yet.")
            label.align(lv.ALIGN.CENTER, 0, 0)
            return

        title = lv.label(scr)
        title.set_text(self.loaded_script_name or self.selected_script)
        title.align(lv.ALIGN.TOP_MID, 0, 65)

        info = lv.label(scr)

        if self.loaded_script_type == "cobra":
            info.set_text(
                "Type: COBRA CSV\n"
                "File: %s\n"
                "Firmware: %s\n"
                "Trigger Channel: %s\n"
                "Trigger Button: %s\n"
                "Audio: %s\n"
                "Events: %d\n"
                "Pyro Events: %d\n"
                "DMX/Other: %d\n"
                "Channels Used: %d\n"
                "Open Cues: %d/%d +%d" % (
                    self.selected_script,
                    self.loaded_script_firmware or "--",
                    self.loaded_script_trigger_channel or "--",
                    self.loaded_script_trigger_button or "--",
                    self.loaded_script_audio or "--",
                    self.loaded_script_event_count,
                    self.loaded_script_fire_count,
                    self.loaded_script_dmx_count,
                    self.loaded_script_channel_count,
                    self.open_script_cue_count,
                    self.loaded_script_fire_count,
                    self.extra_connected_cue_count
                )
            )
        else:
            info.set_text(
                "File: %s\n"
                "Type: Unknown CSV" % self.selected_script
            )

        info.align(lv.ALIGN.CENTER, 0, 35)

    def show_load_script(self, event=None):
        scr = self.new_screen("Load Script", "load_script")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)
        self.make_button(scr, "REFRESH", 180, 400, 160, 55, self.show_load_script)

        title = lv.label(scr)
        title.set_text("Scripts on SD Card")
        title.set_pos(30, 55)

        files = self.scan_sd_for_scripts()

        if not files:
            label = lv.label(scr)
            label.set_text("No .csv/.cvs files found on SD card.")
            label.align(lv.ALIGN.CENTER, 0, 0)
            return

        x0 = 40
        y0 = 95
        w = 330
        h = 55
        gap_x = 25
        gap_y = 15

        for i, filename in enumerate(files[:10]):
            row = i // 2
            col = i % 2

            x = x0 + col * (w + gap_x)
            y = y0 + row * (h + gap_y)

            btn = lv.button(scr)
            btn.set_pos(x, y)
            btn.set_size(w, h)

            lbl = lv.label(btn)
            lbl.set_text(filename)
            lbl.center()

            btn.add_event_cb(
                self.make_script_cb(filename),
                lv.EVENT.CLICKED,
                None
            )

    def scan_sd_for_scripts(self):
        if sdcard_driver is None:
            print("SD driver not loaded")
            return []

        if not hasattr(sdcard_driver, "sd") or sdcard_driver.sd is None:
            print("SD card not detected")
            return []

        try:
            os.mount(sdcard_driver.sd, "/sd")
        except OSError:
            pass
        except Exception as e:
            print("SD mount failed:", e)
            return []

        try:
            files = os.listdir("/sd")
        except Exception as e:
            print("SD list failed:", e)
            return []

        scripts = []

        for f in files:
            name = str(f)
            lower = name.lower()

            if lower.endswith(".csv") or lower.endswith(".cvs"):
                scripts.append(name)

        scripts.sort()
        return scripts

    def make_script_cb(self, filename):
        def cb(event):
            self.select_script(filename)

        return cb

    def select_script(self, filename):
        self.selected_script = filename
        self.clear_loaded_script(filename)

        if self.is_cobra_script_file(filename):
            self.load_cobra_script(filename)
        else:
            self.set_status("Selected script: " + filename)

        print("Selected script:", filename)
        self.update_script_fire_button()
        self.show_view_script()

    def is_cobra_script_file(self, filename):
        path = "/sd/" + filename
        has_trigger_header = False
        has_event_header = False

        try:
            f = open(path, "r")
        except Exception as e:
            print("Script type check open failed:", e)
            return False

        try:
            for _ in range(40):
                line = f.readline()
                if not line:
                    break

                line = line.strip()

                if line.startswith("#Trigger Channel"):
                    has_trigger_header = True
                elif line.startswith("#Event Time"):
                    has_event_header = True

                if has_trigger_header and has_event_header:
                    try:
                        f.close()
                    except:
                        pass
                    return True
        except Exception as e:
            print("Script type check failed:", e)

        try:
            f.close()
        except:
            pass

        return False

    def csv_split_simple(self, line):
        out = []
        cur = ""
        in_quotes = False

        for ch in line:
            if ch == '"':
                in_quotes = not in_quotes
            elif ch == "," and not in_quotes:
                out.append(cur.strip())
                cur = ""
            else:
                cur += ch

        out.append(cur.strip())
        return out

    def load_cobra_script(self, filename):
        self.loaded_script_type = "cobra"

        path = "/sd/" + filename

        try:
            f = open(path, "r")
        except Exception as e:
            print("Failed to open COBRA script:", e)
            self.set_status("COBRA script open failed")
            return

        try:
            trigger_header = None
            event_header_seen = False

            while True:
                line = f.readline()

                if not line:
                    break

                line = line.strip()

                if not line:
                    continue

                if line.upper().startswith("END"):
                    break

                if line.startswith("#@firmware"):
                    first_cell = self.csv_split_simple(line)[0]
                    self.loaded_script_firmware = first_cell.replace("#@firmware", "").strip()
                    continue

                if line.startswith("#Trigger Channel"):
                    trigger_header = self.csv_split_simple(line)
                    continue

                if trigger_header is not None and self.loaded_script_name == filename:
                    values = self.csv_split_simple(line)

                    try:
                        idx_trigger_channel = trigger_header.index("#Trigger Channel")
                        self.loaded_script_trigger_channel = values[idx_trigger_channel]
                    except:
                        pass

                    try:
                        idx_trigger_button = trigger_header.index("#Trigger Button")
                        self.loaded_script_trigger_button = values[idx_trigger_button]
                    except:
                        pass

                    try:
                        idx_audio = trigger_header.index("#AudioBox Filename")
                        self.loaded_script_audio = values[idx_audio]
                    except:
                        pass

                    try:
                        idx_script_name = trigger_header.index("#Script Name")
                        name = values[idx_script_name]
                        if name:
                            self.loaded_script_name = name
                    except:
                        pass

                    trigger_header = None
                    continue

                if line.startswith("#Event Time"):
                    event_header_seen = True
                    continue

                if event_header_seen:
                    values = self.csv_split_simple(line)

                    if len(values) < 4:
                        continue

                    event_time = values[0]
                    channel = values[1]
                    cue = values[2]

                    if not event_time:
                        continue

                    self.loaded_script_event_count += 1

                    if channel and cue:
                        self.loaded_script_fire_count += 1

                        try:
                            ch = int(channel)
                            cq = int(cue)

                            self.loaded_script_pyro_events.append({
                                "event_time": event_time,
                                "channel": ch,
                                "cue": cq,
                                "open": None
                            })

                            if ch not in self.loaded_script_channels:
                                self.loaded_script_channels.append(ch)

                        except:
                            pass

                    else:
                        self.loaded_script_dmx_count += 1

            self.loaded_script_channels.sort()
            self.loaded_script_channel_count = len(self.loaded_script_channels)
            self.build_script_fire_steps()
            self.update_open_script_cues()
            self.update_extra_connected_cues()

            self.set_status("Loaded COBRA script")

        except Exception as e:
            print("COBRA script parse failed:", e)
            self.set_status("COBRA parse failed")

        try:
            f.close()
        except:
            pass

    # -------------------------
    # Placeholder screens
    # -------------------------

    def show_settings(self, event=None):
        scr = self.new_screen("Settings", "settings")

        title = lv.label(scr)
        title.set_text("Module Continuity")
        title.set_pos(40, 85)

        self.auto_continuity_checkbox = lv.checkbox(scr)
        self.auto_continuity_checkbox.set_pos(45, 130)
        self.auto_continuity_checkbox.set_text("Auto check every 4 seconds")

        if self.auto_module_continuity:
            self.auto_continuity_checkbox.add_state(lv.STATE.CHECKED)
        else:
            self.auto_continuity_checkbox.remove_state(lv.STATE.CHECKED)

        self.auto_continuity_checkbox.add_event_cb(
            self.toggle_auto_module_continuity,
            lv.EVENT.CLICKED,
            None
        )

        self.no_script_color_checkbox = lv.checkbox(scr)
        self.no_script_color_checkbox.set_pos(45, 175)
        self.no_script_color_checkbox.set_text("Color modules without script")

        if self.color_cues_without_script:
            self.no_script_color_checkbox.add_state(lv.STATE.CHECKED)
        else:
            self.no_script_color_checkbox.remove_state(lv.STATE.CHECKED)

        self.no_script_color_checkbox.add_event_cb(
            self.toggle_color_cues_without_script,
            lv.EVENT.CLICKED,
            None
        )

        radio_title = lv.label(scr)
        radio_title.set_text("Radio")
        radio_title.set_pos(430, 85)

        radio_info = get_local_radio_info(300)
        radio_version = "--"
        if radio_info:
            radio_version = radio_info.get("firmware_version", "") or "--"

        radio_version_label = lv.label(scr)
        radio_version_label.set_text("Radio FW: %s" % radio_version)
        radio_version_label.set_pos(430, 105)

        self.make_button(
            scr,
            "RADIO UPDATE",
            430,
            135,
            250,
            55,
            self.show_radio_update
        )

        self.make_button(
            scr,
            "STATS",
            690,
            135,
            90,
            55,
            self.show_radio_stats
        )

        network_label = lv.label(scr)
        network_label.set_text("Network: %d  Channel: %d" % (self.radio_network_id, self.radio_channel))
        network_label.set_pos(430, 205)

        self.make_button(
            scr,
            "NETWORK ID",
            430,
            235,
            160,
            55,
            self.show_change_network
        )

        self.make_button(
            scr,
            "PAIR",
            610,
            235,
            130,
            55,
            self.pair_modules_to_network
        )

        self.make_button(
            scr,
            "SCAN CHANNELS",
            430,
            300,
            250,
            50,
            self.scan_radio_channels
        )

        self.make_button(
            scr,
            "OTA",
            690,
            300,
            90,
            50,
            self.show_e77_update
        )

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)

    def format_radio_stats_text(self, stats):
        if not stats:
            return "Radio stats unavailable."

        tx_busy = int(stats.get("tx_busy", 0))
        cca_ok = int(stats.get("cca_ok", 0))
        cca_busy = int(stats.get("cca_busy", 0))
        address = int(stats.get("address", 0))

        return (
            "TX ok: %d    timeout: %d    busy: %d\n"
            "RX ok: %d    timeout: %d\n"
            "RX CRC errors: %d    bad length: %d\n"
            "RX duplicate: %d    wrong net: %d\n"
            "CCA ok: %d    busy: %d\n"
            "RSSI: %d dBm    TX power: %d dBm\n"
            "Channel: %d    Payload: %d\n"
            "Address: 0x%08X"
        ) % (
            int(stats.get("tx_ok", 0)),
            int(stats.get("tx_timeout", 0)),
            tx_busy,
            int(stats.get("rx_ok", 0)),
            int(stats.get("rx_timeout", 0)),
            int(stats.get("rx_crc_error", 0)),
            int(stats.get("rx_bad_len", 0)),
            int(stats.get("rx_duplicate", 0)),
            int(stats.get("rx_wrong_network", 0)),
            cca_ok,
            cca_busy,
            int(stats.get("last_rssi", 0)),
            int(stats.get("tx_power", 0)),
            int(stats.get("channel", 0)),
            int(stats.get("max_payload", 0)),
            address & 0xFFFFFFFF,
        )

    def show_radio_stats(self, event=None):
        scr = self.new_screen("Radio Stats", "radio_stats")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_settings)
        self.make_button(scr, "REFRESH", 180, 400, 160, 55, self.show_radio_stats)
        self.make_button(scr, "CLEAR", 360, 400, 160, 55, self.clear_radio_stats)

        stats = None
        try:
            stats = get_local_radio_stats(600)
        except Exception as e:
            print("Radio stats failed:", e)

        label = lv.label(scr)
        label.set_text(self.format_radio_stats_text(stats))
        label.set_pos(55, 85)

        if stats:
            self.set_status("Radio stats loaded")
        else:
            self.set_status("Radio stats unavailable")

    def clear_radio_stats(self, event=None):
        try:
            if clear_local_radio_stats(600):
                self.set_status("Radio stats cleared")
            else:
                self.set_status("Radio stats clear failed")
        except Exception as e:
            print("Radio stats clear failed:", e)
            self.set_status("Radio stats clear failed")

        self.show_radio_stats()

    def file_exists(self, path):
        try:
            os.stat(path)
            return True
        except:
            return False

    def find_e77_update_manifest(self):
        self.mount_sd_for_radio_update()
        paths = (
            "/sd/e77_update.json",
            "/sd/firmware/e77_update.json",
            "/e77_update.json",
            "e77_update.json",
        )

        for path in paths:
            if self.file_exists(path):
                try:
                    with open(path, "r") as f:
                        manifest = json.load(f)
                    manifest["manifest_path"] = path
                    return manifest
                except Exception as e:
                    print("E77 update manifest read failed:", e)
                    return None

        return None

    def resolve_e77_update_source(self, manifest_path, source):
        if source.startswith("/"):
            return source

        base = "/sd"
        if manifest_path.startswith("/sd/firmware/"):
            base = "/sd/firmware"
        elif manifest_path.startswith("/"):
            base = "/"

        if base == "/":
            return "/" + source
        return base + "/" + source

    def module_supports_e77_update(self, module, manifest=None):
        app_name = str(module.get("app_name", "") or "")
        target = ""

        if manifest:
            target = str(manifest.get("target", "") or "")

        if target:
            if target == "slave":
                return app_name == "slave"
            if target == "dummy":
                return app_name == "dummy"
            if target == "nrf-dummy":
                return app_name == "nrf-dummy"
            if target == "e77":
                return app_name == "e77-micropython"
            return False

        if app_name == "e77-micropython":
            return True

        return False

    def show_e77_update(self, event=None):
        scr = self.new_screen("" \
        "OTA", "e77_update")
        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_settings)

        self.load_modules_from_db()
        manifest = self.find_e77_update_manifest()
        modules = [
            module for module in self.modules
            if self.module_supports_e77_update(module, manifest)
        ]

        label = lv.label(scr)
        if not manifest:
            label.set_text(
                "No E77 update manifest found.\n"
                "Use /sd/e77_update.json."
            )
            label.align(lv.ALIGN.CENTER, 0, -45)
            return

        files = manifest.get("files", [])
        label.set_text(
                "Manifest: %s\nTarget: %s\nVersion: %s\nFiles: %d\nModules: %d" %
            (
                manifest.get("manifest_path", "--"),
                manifest.get("target", "e77"),
                manifest.get("version", "--"),
                len(files),
                len(modules)
            )
        )
        label.align(lv.ALIGN.CENTER, 0, -60)

        self.make_button(
            scr,
            "START UPDATE",
            250,
            285,
            300,
            75,
            self.run_e77_update
        )

    def run_e77_update(self, event=None):
        manifest = self.find_e77_update_manifest()
        if not manifest:
            self.set_status("No E77 update manifest")
            self.show_e77_update()
            return

        files = manifest.get("files", [])
        if not files:
            self.set_status("E77 update manifest empty")
            self.show_e77_update()
            return

        self.load_modules_from_db()
        modules = [
            module for module in self.modules
            if self.module_supports_e77_update(module, manifest)
        ]
        if not modules:
            self.set_status("No E77 modules found")
            self.show_e77_update()
            return

        total_ok = 0
        total_failed = 0

        for module in modules:
            uid_text = module.get("unique_id", "0x00000000")
            try:
                uid = int(uid_text, 16) & 0xFFFFFFFF
            except:
                total_failed += 1
                continue

            module_ok = True
            transfer_id = random.getrandbits(16) & 0xFFFF
            if transfer_id == 0:
                transfer_id = 1

            for index, item in enumerate(files):
                source = item.get("source", "")
                target = item.get("target", "")
                if not source or not target:
                    module_ok = False
                    total_failed += 1
                    continue

                source_path = self.resolve_e77_update_source(
                    manifest.get("manifest_path", ""),
                    source
                )
                if not self.file_exists(source_path):
                    print("E77 update source missing:", source_path)
                    module_ok = False
                    total_failed += 1
                    continue

                file_transfer_id = (transfer_id + index) & 0xFFFF
                if file_transfer_id == 0:
                    file_transfer_id = 1

                self.show_loading_screen(
                    "E77 update...\n%s -> %s" %
                    (self.format_uid(uid_text), target)
                )

                def progress(done, size):
                    if hasattr(self, "loading_label"):
                        self.loading_label.set_text(
                            "E77 update...\n%s %d/%d" %
                            (self.format_uid(uid_text), done, size)
                        )
                        self.flush_ui()

                if not nfcp.e77_update_send_file_for_uid(
                    uid,
                    source_path,
                    target,
                    file_transfer_id,
                    progress
                ):
                    module_ok = False
                    total_failed += 1
                    break

            if module_ok:
                self.show_loading_screen(
                    "E77 update...\nRebooting %s" %
                    self.format_uid(uid_text)
                )
                nfcp.e77_update_reboot_for_uid(uid)
                total_ok += 1

        self.show_e77_update()
        self.set_status("E77 update complete - %d ok, %d failed" % (total_ok, total_failed))

    def mount_sd_for_radio_update(self):
        if sdcard_driver is None:
            return

        if not hasattr(sdcard_driver, "sd") or sdcard_driver.sd is None:
            return

        try:
            os.mount(sdcard_driver.sd, "/sd")
        except OSError:
            pass
        except Exception as e:
            print("SD mount failed:", e)

    def find_radio_update(self):
        try:
            import e77_bootloader
            self.mount_sd_for_radio_update()
            return e77_bootloader.find_firmware()
        except Exception as e:
            print("Radio firmware check failed:", e)
            return None

    def show_radio_update(self, event=None):
        scr = self.new_screen("Radio Update", "radio_update")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_settings)

        info = self.find_radio_update()

        label = lv.label(scr)
        if info:
            version = info.get("version", "")
            if version:
                label.set_text(
                    "Firmware found:\n%s\nVersion: %s" %
                    (info.get("path", "--"), version)
                )
            else:
                label.set_text("Firmware found:\n%s" % info.get("path", "--"))

            self.make_button(
                scr,
                "START UPDATE",
                250,
                285,
                300,
                75,
                self.run_radio_update
            )
        else:
            label.set_text(
                "No radio firmware found.\n"
                "Use /sd/latest.json or /sd/firmware/latest.json."
            )

        label.align(lv.ALIGN.CENTER, 0, -45)

    def restore_radio_uart(self):
        global uart, radio_rx

        try:
            uart = UART(
                UART_ID,
                baudrate=BAUDRATE,
                tx=Pin(TX_PIN),
                rx=Pin(RX_PIN)
            )
            radio_rx = e77_radio.E77FrameParser()
            drain_uart()
        except Exception as e:
            print("Radio UART restore failed:", e)

    def run_radio_update(self, event=None):
        try:
            import e77_bootloader
        except Exception as e:
            print("Radio updater import failed:", e)
            self.set_status("Radio updater missing")
            return

        self.mount_sd_for_radio_update()
        info = e77_bootloader.find_firmware()
        if not info:
            self.set_status("No radio firmware found")
            self.show_radio_update()
            return

        self.show_loading_screen("Radio update...\nStarting...")

        last_progress_ms = {"value": 0}

        def progress(message):
            print("Radio update:", message)

            now = time.ticks_ms()
            if time.ticks_diff(now, last_progress_ms["value"]) < 500:
                return

            last_progress_ms["value"] = now

            try:
                if hasattr(self, "loading_label"):
                    self.loading_label.set_text("Radio update...\n" + message)
                    self.loading_label.align(lv.ALIGN.CENTER, 0, -20)
                    self.flush_ui()
            except Exception:
                pass

        result = None
        try:
            result = e77_bootloader.flash_first_available(
                UART_ID,
                TX_PIN,
                RX_PIN,
                app_baud=BAUDRATE,
                boot_baud=RADIO_BOOTLOADER_BAUD,
                progress_cb=progress
            )
        except Exception as e:
            print("Radio update failed:", e)
            self.restore_radio_uart()
            self.show_radio_update()
            self.set_status("Radio update failed")
            return

        self.restore_radio_uart()
        self.show_radio_update()

        if result:
            self.set_status("Radio update complete")
        else:
            self.set_status("Radio update finished")

    def show_change_network(self, event=None):
        scr = self.new_screen("Radio Network", "radio_network")

        self.network_edit_value = self.radio_network_id
        self.network_edit_text = str(self.radio_network_id)

        self.make_button(scr, "BACK", 20, 400, 120, 55, self.show_settings)
        self.make_button(scr, "SAVE", 650, 400, 120, 55, self.save_network_change)

        title = lv.label(scr)
        title.set_text("Network ID")
        title.align(lv.ALIGN.TOP_MID, 0, 75)

        self.network_edit_box = lv.obj(scr)
        self.network_edit_box.set_size(260, 70)
        self.network_edit_box.align(lv.ALIGN.TOP_MID, 0, 110)
        self.network_edit_box.set_style_bg_color(lv.color_hex(0x202020), 0)
        self.network_edit_box.set_style_border_color(lv.color_hex(0xFFFFFF), 0)
        self.network_edit_box.set_style_border_width(2, 0)
        self.network_edit_box.set_style_radius(8, 0)
        self.network_edit_box.set_style_pad_all(0, 0)

        self.network_edit_label = lv.label(self.network_edit_box)
        self.network_edit_label.set_text(self.network_edit_text)
        self.network_edit_label.center()

        self.build_network_number_pad(scr)

    def build_network_number_pad(self, parent):
        x0 = 145
        y0 = 205
        btn_w = 150
        btn_h = 42
        gap = 12

        keys = [
            ["1", "2", "3"],
            ["4", "5", "6"],
            ["7", "8", "9"],
            ["CLR", "0", "DEL"]
        ]

        for row in range(4):
            for col in range(3):
                key = keys[row][col]
                x = x0 + col * (btn_w + gap)
                y = y0 + row * (btn_h + gap)
                self.make_button(parent, key, x, y, btn_w, btn_h, self.make_network_key_cb(key))

    def make_network_key_cb(self, key):
        def cb(event):
            self.network_key_pressed(key)

        return cb

    def network_key_pressed(self, key):
        if key == "CLR":
            self.network_edit_text = ""
        elif key == "DEL":
            if len(self.network_edit_text) > 0:
                self.network_edit_text = self.network_edit_text[:-1]
        else:
            if len(self.network_edit_text) < 5:
                if self.network_edit_text == "0":
                    self.network_edit_text = key
                else:
                    self.network_edit_text += key

        if self.network_edit_text == "":
            self.network_edit_value = 0
        else:
            try:
                self.network_edit_value = int(self.network_edit_text)
                if self.network_edit_value > 0xFFFF:
                    self.network_edit_value = 0xFFFF
                    self.network_edit_text = str(self.network_edit_value)
            except:
                self.network_edit_value = 0
                self.network_edit_text = ""

        if hasattr(self, "network_edit_label"):
            self.network_edit_label.set_text(self.network_edit_text or "_")
            self.network_edit_label.center()

    def save_network_change(self, event=None):
        if self.network_edit_value < 0 or self.network_edit_value > 0xFFFF:
            self.set_status("Invalid network")
            return

        new_network = self.network_edit_value & 0xFFFF

        if not set_local_radio_network(new_network):
            self.set_status("Radio network set failed")
            return

        self.radio_network_id = new_network
        self.settings["radio_network_id"] = self.radio_network_id

        try:
            save_app_settings(self.settings)
        except Exception as e:
            print("Settings save failed:", e)
            self.set_status("Settings save failed")
            return

        self.set_status("Network changed to %d" % self.radio_network_id)
        self.show_settings()

    def pair_modules_to_network(self, event=None):
        target_network = self.radio_network_id & 0xFFFF
        pair_network = e77_radio.DEFAULT_NETWORK_ID & 0xFFFF

        self.show_loading_screen("Pairing modules...\nScanning default network...")

        paired = 0
        failed = 0
        discovered_count = 0
        paired_uids = []

        try:
            if not set_local_radio_network(pair_network):
                self.show_settings()
                self.set_status("Pair failed - radio network")
                return

            devices = nfcp.discover()
            discovered_count = len(devices)

            for dev in devices:
                uid_text = dev.get("unique_id", "0x00000000")
                try:
                    uid = int(uid_text, 16) & 0xFFFFFFFF
                except:
                    failed += 1
                    continue

                if nfcp.request_set_network_for_uid(uid, target_network):
                    dev["radio_network_id"] = target_network
                    paired_uids.append(uid_text)
                    paired += 1
                else:
                    failed += 1

            try:
                db = nfcp.load_device_db()
                idx = nfcp.index_db_by_unique(db)
                for uid_text in paired_uids:
                    if uid_text in idx:
                        idx[uid_text]["radio_network_id"] = target_network
                db["devices"] = sorted(
                    idx.values(),
                    key=lambda d: self.group_to_int_value(d.get("group_id", None))
                )
                nfcp.save_device_db(db)
            except Exception as e:
                print("Pair DB update failed:", e)

            set_local_radio_network(target_network)
            self.load_modules_from_db()
        except Exception as e:
            print("Pair modules failed:", e)
            failed += 1
            try:
                set_local_radio_network(target_network)
            except:
                pass

        self.show_settings()
        self.set_status(
            "Pair complete - %d/%d paired, %d failed" %
            (paired, discovered_count, failed)
        )

    def scan_radio_channels(self, event=None):
        self.show_loading_screen("Scanning channels...\nChecking noise...")

        try:
            results = scan_local_radio_channels()
        except Exception as e:
            print("Channel scan failed:", e)
            results = []

        self.channel_scan_results = results
        self.show_channel_scan_results()

        if results:
            self.set_status("Channel scan complete")
        else:
            self.set_status("Channel scan failed")

    def show_channel_scan_results(self, event=None):
        scr = self.new_screen("Best Radio Channels", "channel_scan")
        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_settings)

        results = self.channel_scan_results or []
        if not results:
            label = lv.label(scr)
            label.set_text("No channel scan results.")
            label.align(lv.ALIGN.CENTER, 0, 0)
            return

        title = lv.label(scr)
        title.set_text("Select a channel to switch all discovered modules")
        title.set_pos(35, 55)

        x0 = 35
        y0 = 95
        w = 220
        h = 55
        gap_x = 25
        gap_y = 14

        for i, item in enumerate(results[:10]):
            row = i // 3
            col = i % 3
            channel = int(item.get("channel", 0))
            score = int(item.get("score", 0))
            busy = int(item.get("busy", 0))
            count = int(item.get("count", 0))
            if count:
                text = "CH %d  Q %d\nmax %d avg %d b%d/%d" % (
                    channel,
                    score,
                    int(item.get("max", 0)),
                    int(item.get("avg", 0)),
                    busy,
                    count
                )
            else:
                text = "CH %d\nmax %d avg %d" % (
                    channel,
                    int(item.get("max", 0)),
                    int(item.get("avg", 0))
                )
            self.make_button(
                scr,
                text,
                x0 + col * (w + gap_x),
                y0 + row * (h + gap_y),
                w,
                h,
                self.make_channel_select_cb(channel)
            )

    def make_channel_select_cb(self, channel):
        def cb(event):
            self.apply_radio_channel(channel)

        return cb

    def apply_radio_channel(self, channel):
        try:
            channel = int(channel)
        except:
            self.set_status("Invalid channel")
            return

        if channel < 0 or channel > 100:
            self.set_status("Invalid channel")
            return

        self.load_modules_from_db()
        modules = list(self.modules)
        ok_count = 0
        failed_count = 0
        changed_uids = []

        self.show_loading_screen("Switching channel...\nModules first...")

        for module in modules:
            uid_text = module.get("unique_id", "0x00000000")
            try:
                uid = int(uid_text, 16) & 0xFFFFFFFF
            except:
                failed_count += 1
                continue

            if nfcp.request_set_radio_channel_for_uid(uid, channel):
                changed_uids.append(uid_text)
                ok_count += 1
            else:
                failed_count += 1

        if failed_count:
            self.show_channel_scan_results()
            self.set_status(
                "Channel not changed - %d ok, %d failed" %
                (ok_count, failed_count)
            )
            return

        if not set_local_radio_channel(channel):
            self.show_channel_scan_results()
            self.set_status("Bridge channel set failed")
            return

        self.radio_channel = channel
        self.settings["radio_channel"] = self.radio_channel

        try:
            save_app_settings(self.settings)
        except Exception as e:
            print("Settings save failed:", e)
            self.set_status("Settings save failed")
            return

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
            for uid_text in changed_uids:
                if uid_text in idx:
                    idx[uid_text]["radio_channel"] = channel
            db["devices"] = sorted(
                idx.values(),
                key=lambda d: self.group_to_int_value(d.get("group_id", None))
            )
            nfcp.save_device_db(db)
        except Exception as e:
            print("Channel DB update failed:", e)

        self.show_settings()
        self.set_status("Channel changed to %d for %d modules" % (channel, ok_count))

    def toggle_auto_module_continuity(self, event=None):
        checked = not self.auto_module_continuity

        try:
            target = event.get_target()
            if hasattr(target, "has_state"):
                checked = target.has_state(lv.STATE.CHECKED)
        except:
            pass

        self.auto_module_continuity = bool(checked)
        self.settings["auto_module_continuity"] = self.auto_module_continuity

        try:
            save_app_settings(self.settings)
            self.set_status(
                "Auto continuity enabled"
                if self.auto_module_continuity
                else "Auto continuity disabled"
            )
        except Exception as e:
            print("Settings save failed:", e)
            self.set_status("Settings save failed")

    def toggle_color_cues_without_script(self, event=None):
        checked = not self.color_cues_without_script

        try:
            target = event.get_target()
            if hasattr(target, "has_state"):
                checked = target.has_state(lv.STATE.CHECKED)
        except:
            pass

        self.color_cues_without_script = bool(checked)
        self.settings["color_cues_without_script"] = self.color_cues_without_script

        try:
            save_app_settings(self.settings)
            self.set_status(
                "No-script module colors enabled"
                if self.color_cues_without_script
                else "No-script module colors disabled"
            )
        except Exception as e:
            print("Settings save failed:", e)
            self.set_status("Settings save failed")

    def show_manual_fire(self, event=None):
        if not self.armed:
            self.set_status("Manual fire disabled - not armed")
            return

        self.load_modules_from_db()
        self.manual_fire_modules = list(self.modules)

        if not self.manual_fire_modules:
            self.set_status("Manual fire disabled - no modules")
            return

        if self.manual_fire_index < 0:
            self.manual_fire_index = 0
        if self.manual_fire_index >= len(self.manual_fire_modules):
            self.manual_fire_index = len(self.manual_fire_modules) - 1

        scr = self.new_screen("Manual Fire", "manual_fire")

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.leave_manual_fire)

        self.make_button(scr, "CHANNEL +", 35, 95, 170, 95, self.manual_channel_up)
        self.make_button(scr, "CHANNEL -", 35, 230, 170, 95, self.manual_channel_down)

        self.manual_module_label = lv.label(scr)
        self.manual_module_label.set_pos(250, 72)

        self.manual_summary_label = lv.label(scr)
        self.manual_summary_label.set_pos(250, 102)

        channel_title = lv.label(scr)
        channel_title.set_text("CHANNEL")
        channel_title.align(lv.ALIGN.TOP_RIGHT, -60, 75)

        self.manual_channel_label = lv.label(scr)
        self.manual_channel_label.set_pos(610, 115)
        try:
            self.manual_channel_label.set_style_text_font(lv.font_montserrat_48, 0)
        except:
            pass

        self.build_manual_fire_cue_grid(scr)
        self.refresh_manual_fire_screen()

    def leave_manual_fire(self, event=None):
        self.manual_last_fire_uid = None
        self.manual_last_fire_key = None
        self.clear_trellis_leds()
        self.show_main_menu()

    def get_manual_fire_module(self):
        if not self.manual_fire_modules:
            return None

        if self.manual_fire_index < 0:
            self.manual_fire_index = 0
        if self.manual_fire_index >= len(self.manual_fire_modules):
            self.manual_fire_index = len(self.manual_fire_modules) - 1

        return self.manual_fire_modules[self.manual_fire_index]

    def manual_channel_up(self, event=None):
        if not self.manual_fire_modules:
            return

        self.manual_fire_index += 1
        if self.manual_fire_index >= len(self.manual_fire_modules):
            self.manual_fire_index = 0

        self.refresh_manual_fire_screen()

    def manual_channel_down(self, event=None):
        if not self.manual_fire_modules:
            return

        self.manual_fire_index -= 1
        if self.manual_fire_index < 0:
            self.manual_fire_index = len(self.manual_fire_modules) - 1

        self.refresh_manual_fire_screen()

    def build_manual_fire_cue_grid(self, parent):
        self.manual_cue_buttons = []
        self.manual_cue_labels = []

        x0 = 240
        y0 = 155
        btn_w = 110
        btn_h = 70
        gap_x = 12
        gap_y = 12

        for i in range(16):
            row = i // 4
            col = i % 4
            x = x0 + col * (btn_w + gap_x)
            y = y0 + row * (btn_h + gap_y)

            btn = lv.button(parent)
            btn.set_pos(x, y)
            btn.set_size(btn_w, btn_h)

            lbl = lv.label(btn)
            lbl.set_text("%02d" % (i + 1))
            lbl.center()

            self.manual_cue_buttons.append(btn)
            self.manual_cue_labels.append(lbl)

    def make_manual_fire_cb(self, cue_index):
        def cb(event):
            self.fire_manual_cue(cue_index)

        return cb

    def refresh_manual_fire_screen(self):
        module = self.get_manual_fire_module()
        if not module:
            self.clear_trellis_leds()
            return

        self.selected_module = module
        self.continuity = self.get_module_saved_continuity(module)

        channel_text = self.group_to_int_display(module.get("group_id", "--"))
        uid_text = module.get("unique_id", "0x00000000")
        cue_count = self.get_module_cue_count(module)
        continuity = self.get_module_continuity_list(module)
        good = 0

        if hasattr(self, "manual_channel_label"):
            self.manual_channel_label.set_text(str(channel_text))

        if hasattr(self, "manual_module_label"):
            self.manual_module_label.set_text(
                "UID: %s   Cues: %d" %
                (self.format_uid(uid_text), cue_count)
            )

        for i in range(16):
            cue_ok = i < len(continuity) and bool(continuity[i])
            cue_exists = i < cue_count
            if cue_ok:
                good += 1

            if hasattr(self, "manual_cue_labels") and i < len(self.manual_cue_labels):
                if cue_exists:
                    self.manual_cue_labels[i].set_text("%02d" % (i + 1))
                else:
                    self.manual_cue_labels[i].set_text("--")
                self.manual_cue_labels[i].center()

            if hasattr(self, "manual_cue_buttons") and i < len(self.manual_cue_buttons):
                if not cue_exists:
                    color = lv.color_hex(0x4A4A4A)
                elif cue_ok:
                    color = lv.color_hex(0x1F7A1F)
                else:
                    color = lv.color_hex(0x7A1F1F)

                self.manual_cue_buttons[i].set_style_bg_color(color, 0)

        if hasattr(self, "manual_summary_label"):
            self.manual_summary_label.set_text(
                "Continuity: %d / %d   Module %d / %d" %
                (
                    good,
                    cue_count,
                    self.manual_fire_index + 1,
                    len(self.manual_fire_modules)
                )
            )

        self.update_trellis_continuity_leds(continuity, cue_count)
        self.update_manual_fire_last_indicator(force=True)
        self.set_status("Manual fire channel %s selected" % channel_text)

    def get_manual_cue_base_color(self, cue_index, continuity, cue_count):
        cue_exists = cue_index < cue_count
        cue_ok = cue_index < len(continuity) and bool(continuity[cue_index])

        if not cue_exists:
            return lv.color_hex(0x4A4A4A)
        if cue_ok:
            return lv.color_hex(0x1F7A1F)
        return lv.color_hex(0x7A1F1F)

    def manual_last_fire_matches_current_module(self):
        module = self.get_manual_fire_module()
        if not module:
            return False

        uid_text = module.get("unique_id", "0x00000000")
        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            return False

        return self.manual_last_fire_uid == uid

    def update_manual_fire_last_indicator(self, force=False):
        if self.current_view != "manual_fire":
            return

        now = time.ticks_ms()
        if (
            not force and
            time.ticks_diff(now, self.manual_last_fire_blink_ms) < MANUAL_FIRE_BLINK_MS
        ):
            return

        self.manual_last_fire_blink_ms = now
        if force:
            self.manual_last_fire_blink_on = True
        else:
            self.manual_last_fire_blink_on = not self.manual_last_fire_blink_on

        module = self.get_manual_fire_module()
        if not module:
            return

        cue_count = self.get_module_cue_count(module)
        continuity = self.get_module_continuity_list(module)
        blink_key = self.manual_last_fire_key
        blink_visible = (
            blink_key is not None and
            self.manual_last_fire_matches_current_module() and
            0 <= blink_key < min(16, cue_count)
        )

        try:
            if self.trellis:
                self.trellis.clear()
                limit = min(16, cue_count)
                for i in range(limit):
                    lit = i < len(continuity) and bool(continuity[i])
                    if blink_visible and i == blink_key:
                        lit = self.manual_last_fire_blink_on
                    if lit:
                        self.trellis.set_led(i)
                self.trellis.write_display()
        except Exception as e:
            print("Manual Trellis blink failed:", e)

        if force and hasattr(self, "manual_cue_buttons"):
            for i in range(min(16, len(self.manual_cue_buttons))):
                try:
                    self.manual_cue_buttons[i].set_style_bg_color(
                        self.get_manual_cue_base_color(i, continuity, cue_count),
                        0
                    )
                except Exception as e:
                    print("Manual cue indicator update failed:", e)
                    return

    def clear_trellis_leds(self):
        if not self.trellis:
            return

        try:
            self.trellis.clear()
            self.trellis.write_display()
        except Exception as e:
            print("Trellis clear failed:", e)

    def update_trellis_continuity_leds(self, continuity, cue_count):
        if not self.trellis:
            return

        try:
            self.trellis.clear()
            limit = min(16, cue_count)
            for i in range(limit):
                if i < len(continuity) and bool(continuity[i]):
                    self.trellis.set_led(i)
            self.trellis.write_display()
        except Exception as e:
            print("Trellis LED update failed:", e)

    def fire_manual_cue(self, cue_index):
        now = time.ticks_ms()
        if time.ticks_diff(now, self.manual_fire_last_fire_ms) < MANUAL_FIRE_DEBOUNCE_MS:
            return

        module = self.get_manual_fire_module()
        if not module:
            self.set_status("No manual fire channel selected")
            return

        cue_count = self.get_module_cue_count(module)
        if cue_index < 0 or cue_index >= cue_count:
            self.set_status("Manual fire cue out of range")
            return

        self.manual_fire_last_fire_ms = now
        self.selected_module = module
        self.continuity = self.get_module_saved_continuity(module)
        self.fire_cue(cue_index)

        try:
            self.manual_last_fire_uid = int(
                module.get("unique_id", "0x00000000"),
                16
            ) & 0xFFFFFFFF
        except:
            self.manual_last_fire_uid = None
        self.manual_last_fire_key = cue_index
        self.manual_last_fire_blink_on = True
        self.manual_last_fire_blink_ms = now
        self.update_manual_fire_last_indicator(force=True)

    def poll_manual_fire_trellis(self):
        if self.current_view != "manual_fire":
            return
        if not self.trellis:
            return

        try:
            changed = self.trellis.read_switches()
            if not changed:
                return

            for key in range(16):
                if self.trellis.just_pressed(key):
                    self.fire_manual_cue(key)
                    return
        except Exception as e:
            print("Trellis poll failed:", e)
            self.set_status("Trellis poll failed")

    # -------------------------
    # Modules screen helpers
    # -------------------------

    def load_modules_from_db(self):
        try:
            db = nfcp.load_device_db()
            self.modules = db.get("devices", []) if db else []

            self.modules.sort(
                key=lambda m: self.group_to_int_value(
                    m.get("group_id", None)
                )
            )

        except Exception as e:
            print("DB load failed:", e)
            self.modules = []

    def get_discovered_groups(self):
        groups = []

        for module in self.modules:
            gid = module.get("group_id", None)

            if gid is None:
                continue

            group_num = self.parse_group_id(gid)
            if group_num is not None:
                groups.append(group_num)

        groups.sort()
        return groups

    def get_duplicate_discovered_groups(self):
        counts = {}

        for group in self.get_discovered_groups():
            counts[group] = counts.get(group, 0) + 1

        duplicates = []

        for group, count in counts.items():
            if count > 1:
                duplicates.append({
                    "group": group,
                    "count": count
                })

        duplicates.sort(key=lambda x: x["group"])
        return duplicates

    def update_script_module_mismatch(self):
        self.loaded_script_mismatch = []
        self.loaded_script_extra_channels = []
        self.loaded_script_duplicate_channels = []

        if not self.loaded_script_channels:
            return

        discovered_groups = self.get_discovered_groups()
        discovered_unique = []

        for group in discovered_groups:
            if group not in discovered_unique:
                discovered_unique.append(group)

        for ch in self.loaded_script_channels:
            if ch not in discovered_unique:
                self.loaded_script_mismatch.append(ch)

        for group in discovered_unique:
            if group not in self.loaded_script_channels:
                self.loaded_script_extra_channels.append(group)

        self.loaded_script_duplicate_channels = self.get_duplicate_discovered_groups()

    def update_open_script_cues(self):
        open_count = 0

        if not self.loaded_script_pyro_events:
            self.open_script_cue_count = 0
            return

        group_continuity = {}

        for module in self.modules:
            gid = module.get("group_id", None)

            group_id = self.parse_group_id(gid)
            if group_id is None:
                continue

            group_continuity[group_id] = self.get_module_continuity_list(module)

        for event in self.loaded_script_pyro_events:
            channel = event.get("channel")
            cue = event.get("cue")

            if channel not in group_continuity:
                event["open"] = True
                open_count += 1
                continue

            cue_index = cue - 1

            if cue_index < 0 or cue_index >= len(group_continuity[channel]):
                event["open"] = True
                open_count += 1
                continue

            cue_ok = bool(group_continuity[channel][cue_index])

            event["open"] = not cue_ok

            if not cue_ok:
                open_count += 1

        self.open_script_cue_count = open_count

    def update_extra_connected_cues(self):
        self.extra_connected_cue_count = 0

        if not self.loaded_script_pyro_events:
            return

        assigned = {}

        for event in self.loaded_script_pyro_events:
            try:
                ch = int(event.get("channel"))
                cue = int(event.get("cue"))

                if ch not in assigned:
                    assigned[ch] = {}

                assigned[ch][cue] = True
            except:
                pass

        for module in self.modules:
            gid = module.get("group_id", None)

            group_id = self.parse_group_id(gid)
            if group_id is None:
                continue

            if not self.module_has_continuity_data(module):
                continue

            continuity = self.get_module_continuity_list(module)

            for i, cue_ok in enumerate(continuity):
                if not cue_ok:
                    continue

                cue_num = i + 1

                if group_id not in assigned:
                    self.extra_connected_cue_count += 1
                    continue

                if cue_num not in assigned[group_id]:
                    self.extra_connected_cue_count += 1

    def get_module_button_color(self, module):
        if self.module_is_lost(module):
            return lv.color_hex(0x4A4A4A)

        normal_blue = lv.color_hex(0x2F4F8F)
        orange = lv.color_hex(0xC46A00)
        red = lv.color_hex(0x7A1F1F)
        green = lv.color_hex(0x1F7A1F)

        if not self.selected_script:
            if not self.color_cues_without_script:
                return normal_blue

            if not self.module_has_continuity_data(module):
                return normal_blue

            continuity = self.get_module_continuity_list(module)

            if not continuity:
                return normal_blue

            for cue_ok in continuity:
                if not cue_ok:
                    return red

            return green

        gid = module.get("group_id", None)

        group_id = self.parse_group_id(gid)
        if group_id is None:
            return orange

        if self.loaded_script_channels and group_id not in self.loaded_script_channels:
            return orange

        duplicate_groups = self.get_duplicate_discovered_groups()

        for item in duplicate_groups:
            if item.get("group") == group_id:
                return orange

        if not self.module_has_continuity_data(module):
            return normal_blue

        continuity = self.get_module_continuity_list(module)

        cont_map = {}

        for i, cue_ok in enumerate(continuity):
            cont_map[i + 1] = bool(cue_ok)

        assigned_cues = []

        for event in self.loaded_script_pyro_events:
            try:
                ch = int(event.get("channel"))
                cue = int(event.get("cue"))

                if ch == group_id and cue not in assigned_cues:
                    assigned_cues.append(cue)
            except:
                pass

        if not assigned_cues:
            for cue_num, cue_ok in cont_map.items():
                if cue_ok:
                    return orange

            return normal_blue

        for cue_num in assigned_cues:
            cue_ok = bool(cont_map.get(cue_num, False))

            if not cue_ok:
                return red

        for cue_num, cue_ok in cont_map.items():
            if cue_ok and cue_num not in assigned_cues:
                return orange

        return green

    # -------------------------
    # Modules screen
    # -------------------------

    def show_modules_menu(self, event=None, restore_selected_page=True):
        self.load_modules_from_db()
        if restore_selected_page:
            self.set_modules_page_for_uid(self.last_selected_module_uid)
        self.update_script_module_mismatch()
        self.update_open_script_cues()
        self.update_extra_connected_cues()

        scr = self.new_screen("Modules", "modules")

        self.make_button(scr, "BACK", 20, 400, 120, 55, self.show_main_menu)

        self.discover_btn = self.make_button(
            scr,
            "DISCOVER",
            160,
            400,
            150,
            55,
            self.discover_modules
        )

        self.refresh_btn = self.make_button(
            scr,
            "REFRESH",
            330,
            400,
            150,
            55,
            self.refresh_modules_with_continuity
        )

        self.make_button(scr, "RESET", 500, 400, 150, 55, self.reset_modules)

        self.update_modules_busy_buttons()

        if self.selected_script:
            discovered_groups = self.get_discovered_groups()

            script_name = self.loaded_script_name or self.selected_script

            total_script_channels = self.loaded_script_channel_count
            discovered_matching_channels = 0

            for group in discovered_groups:
                if group in self.loaded_script_channels:
                    discovered_matching_channels += 1

            open_cues_loaded = self.open_script_cue_count
            open_cues_total = self.loaded_script_fire_count

            label = lv.label(scr)
            label.set_text(
                "Script: %s | Open Cues: %d/%d +%d | Discovered Channels: %d/%d" %
                (
                    script_name,
                    open_cues_loaded,
                    open_cues_total,
                    self.extra_connected_cue_count,
                    discovered_matching_channels,
                    total_script_channels
                )
            )
            label.set_pos(30, 55)

        else:
            label = lv.label(scr)
            label.set_text("Select a module to view cue continuity")
            label.set_pos(30, 55)

        if not self.modules:
            empty = lv.label(scr)
            empty.set_text("No modules found. Press DISCOVER.")
            empty.align(lv.ALIGN.CENTER, 0, 0)
            return

        page_size = 9
        total_pages = (len(self.modules) + page_size - 1) // page_size

        if total_pages < 1:
            total_pages = 1
        if self.modules_page < 0:
            self.modules_page = 0
        if self.modules_page >= total_pages:
            self.modules_page = total_pages - 1

        page_label = lv.label(scr)
        page_label.set_text("Page %d/%d" % (self.modules_page + 1, total_pages))
        page_label.set_pos(665, 55)

        if total_pages > 1:
            self.make_button(scr, "PREV", 670, 255, 90, 130, self.prev_modules_page)
            self.make_button(scr, "NEXT", 670, 100, 90, 130, self.next_modules_page)

        module_area = lv.obj(scr)
        module_area.set_pos(20, 100)
        module_area.set_size(640, 285)
        module_area.set_style_bg_color(lv.color_hex(0x101010), 0)
        module_area.set_style_border_width(0, 0)
        module_area.set_style_radius(0, 0)
        module_area.set_style_pad_all(0, 0)

        x0 = 0
        y0 = 0
        w = 205
        h = 82
        gap_x = 10
        gap_y = 12

        start_index = self.modules_page * page_size
        page_modules = self.modules[start_index:start_index + page_size]

        for i, module in enumerate(page_modules):
            row = i // 3
            col = i % 3
            uid_text = module.get("unique_id", "0x00000000")

            x = x0 + col * (w + gap_x)
            y = y0 + row * (h + gap_y)

            channel_text = self.group_to_int_display(module.get("group_id", "--"))
            cue_count = module.get("cue_count", 16)
            status_text = " LOST" if self.module_is_lost(module) else ""

            text = "Ch %s  Cues:%s%s\nUID:%s" % (
                channel_text,
                cue_count,
                status_text,
                self.format_uid(uid_text)
            )

            btn = lv.button(module_area)
            btn.set_pos(x, y)
            btn.set_size(w, h)

            btn.set_style_bg_color(
                self.get_module_button_color(module),
                0
            )

            lbl = lv.label(btn)
            lbl.set_text(text)
            lbl.align(lv.ALIGN.TOP_LEFT, 8, 8)

            rssi_lbl = lv.label(btn)
            rssi_lbl.set_text(self.format_rssi(module))
            rssi_lbl.align(lv.ALIGN.BOTTOM_RIGHT, -8, -6)

            btn.add_event_cb(
                self.make_module_cb(module),
                lv.EVENT.CLICKED,
                None
            )

    def next_modules_page(self, event=None):
        self.modules_page += 1
        self.show_modules_menu(restore_selected_page=False)

    def prev_modules_page(self, event=None):
        self.modules_page -= 1
        self.show_modules_menu(restore_selected_page=False)

    def show_loading_screen(self, message="Loading..."):
        scr = self.new_screen("Please Wait", "loading")

        label = lv.label(scr)
        label.set_text(message)
        label.align(lv.ALIGN.CENTER, 0, -20)
        self.loading_label = label

        sub = lv.label(scr)
        sub.set_text("Do not power off.")
        sub.align(lv.ALIGN.CENTER, 0, 25)

        try:
            self.flush_ui()
        except Exception:
            pass

    def flush_ui(self):
        try:
            lv.timer_handler()
        except Exception:
            pass

        try:
            lv.refr_now(None)
        except Exception:
            pass

    def refresh_modules_with_continuity(self, event=None):
        self.show_loading_screen("Refreshing modules...\nChecking continuity...")

        if not self.ensure_bridge_channel():
            self.show_modules_menu()
            self.set_status(self.radio_error)
            return

        try:
            results = nfcp.continuity_scan()
        except Exception as e:
            print("Continuity scan failed:", e)
            self.set_status("Continuity scan failed")
            results = []

        try:
            self.show_loading_screen("Refreshing modules...\nRunning speed tests...")
            speed_results = nfcp.speed_test_scan()
        except Exception as e:
            print("Speed test failed:", e)
            self.set_status("Speed test failed")
            speed_results = []

        try:
            self.show_loading_screen("Refreshing modules...\nReading versions...")
            info_results = nfcp.info_scan()
        except Exception as e:
            print("Info scan failed:", e)
            self.set_status("Info scan failed")
            info_results = []

        self.load_modules_from_db()
        self.update_script_module_mismatch()
        self.update_open_script_cues()
        self.update_extra_connected_cues()

        count = len(results) if results else 0
        speed_count = len(speed_results) if speed_results else 0
        info_count = len(info_results) if info_results else 0
        print("Continuity checked:", count)
        print("Speed tested:", speed_count)
        print("Versions checked:", info_count)

        self.show_modules_menu()
        self.set_status(
            "Refresh complete - %d checked, %d speed, %d versions" %
            (count, speed_count, info_count)
        )

    def reset_modules(self, event=None):
        self.set_status("Clearing module database...")

        if self.armed:
            self.set_armed(False)

        try:
            nfcp.clear_device_db()
        except Exception as e:
            print("Clear DB failed:", e)

        self.modules = []
        self.selected_module = None
        self.continuity = [False] * DEFAULT_CUE_COUNT

        self.show_modules_menu()
        self.update_arm_button()
        self.set_status("Module database cleared")

    def discover_modules(self, event=None):
        if self.discover_busy:
            return

        self.discover_busy = True
        self.show_loading_screen("Discovering modules...\nWaiting for responses...")

        final_status = "Discovery complete"
        cont_count = 0
        speed_count = 0
        info_count = 0

        try:
            if not self.ensure_bridge_channel():
                final_status = self.radio_error
            else:
                self.modules = nfcp.discover()
                self.load_modules_from_db()

                self.show_loading_screen("Discovering modules...\nChecking continuity...")
                cont_results = nfcp.continuity_scan()
                cont_count = len(cont_results) if cont_results else 0

                self.show_loading_screen("Discovering modules...\nRunning speed tests...")
                speed_results = nfcp.speed_test_scan()
                speed_count = len(speed_results) if speed_results else 0

                self.show_loading_screen("Discovering modules...\nReading versions...")
                info_results = nfcp.info_scan()
                info_count = len(info_results) if info_results else 0

                self.load_modules_from_db()
                self.update_script_module_mismatch()
                self.update_open_script_cues()
                self.update_extra_connected_cues()

        except Exception as e:
            print("Discover failed:", e)
            final_status = "Discovery failed"

        self.discover_busy = False
        self.show_modules_menu()
        self.update_arm_button()
        if final_status == "Discovery complete":
            self.set_status(
                "Discovery complete - %d checked, %d speed, %d versions" %
                (cont_count, speed_count, info_count)
            )
        else:
            self.set_status(final_status)

    def make_module_cb(self, module):
        def cb(event):
            self.select_module(module)

        return cb

    # -------------------------
    # Module continuity screen
    # -------------------------

    def select_module(self, module):
        self.selected_module = module
        self.last_selected_module_uid = module.get("unique_id", None)

        self.continuity = self.get_module_saved_continuity(module)
        self.last_module_continuity_auto_ms = time.ticks_ms()

        self.show_loading_screen("Opening module...\nRunning speed, continuity, and version checks...")
        self.request_selected_speed_test()
        self.request_selected_continuity()
        self.request_selected_info()
        self.show_module_continuity()

    def show_module_continuity(self, event=None):
        scr = self.new_screen("Module Continuity", "module_continuity")

        self.make_button(scr, "BACK", 20, 410, 120, 50, self.show_modules_menu)

        self.make_button(
            scr,
            "CHANNEL",
            160,
            410,
            120,
            50,
            self.show_change_group
        )

        self.make_button(
            scr,
            "CHECK",
            600,
            410,
            150,
            50,
            self.request_selected_continuity
        )

        self.make_button(
            scr,
            "UID AUTO",
            300,
            410,
            120,
            50,
            self.reset_selected_uid_to_auto
        )

        uid_text = self.selected_module.get("unique_id", "0x00000000")
        channel_id = self.group_to_int_display(self.selected_module.get("group_id", "--"))
        cue_count = self.get_selected_module_cue_count()

        self.module_label = lv.label(scr)
        self.module_label.set_text(
            "UID: %s   Channel: %s   Cues: %s" % (
                self.format_uid(uid_text),
                channel_id,
                cue_count
            )
        )
        self.module_label.set_pos(25, 55)

        self.arm_label = lv.label(scr)
        self.arm_label.set_text("ARM: ON" if self.armed else "ARM: OFF")
        self.arm_label.set_pos(650, 55)

        self.speed_label = lv.label(scr)
        self.speed_label.set_text(self.format_selected_speed_test())
        self.speed_label.set_pos(300, 82)

        self.radio_link_label = lv.label(scr)
        self.radio_link_label.set_text(self.format_selected_radio_link())
        self.radio_link_label.set_pos(25, 82)

        self.version_label = lv.label(scr)
        self.version_label.set_text(self.format_selected_versions())
        self.version_label.set_pos(25, 102)

        self.identity_label = lv.label(scr)
        self.identity_label.set_text(self.format_selected_identity())
        self.identity_label.set_pos(430, 102)

        self.build_fixed_cue_grid(scr)

        self.summary = lv.label(scr)
        self.summary.set_text("Continuity: 0 / %d" % cue_count)
        self.summary.set_pos(600, 385)

        self.refresh_cues()

    def build_fixed_cue_grid(self, parent):
        self.cue_buttons = []
        self.cue_labels = []

        cue_count = self.get_selected_module_cue_count()

        self.grid_box = lv.obj(parent)
        self.grid_box.set_size(760, 270)
        self.grid_box.set_pos(20, 120)

        self.grid_box.set_style_bg_color(lv.color_hex(0x181818), 0)
        self.grid_box.set_style_pad_all(0, 0)
        self.grid_box.set_style_border_width(0, 0)
        self.grid_box.set_style_radius(0, 0)

        self.grid_box.set_scroll_dir(lv.DIR.VER)
        self.grid_box.set_scrollbar_mode(lv.SCROLLBAR_MODE.AUTO)

        cols = 4
        btn_w = 190
        btn_h = 67

        for i in range(cue_count):
            row = i // cols
            col = i % cols

            btn = lv.button(self.grid_box)
            btn.set_pos(col * btn_w, row * btn_h)
            btn.set_size(btn_w, btn_h)

            btn.set_style_pad_all(0, 0)
            btn.set_style_radius(0, 0)
            btn.set_style_border_width(1, 0)

            lbl = lv.label(btn)
            lbl.set_text("Cue %02d: OPEN" % (i + 1))
            lbl.center()

            self.cue_buttons.append(btn)
            self.cue_labels.append(lbl)

    def make_fire_cb(self, cue_index):
        def cb(event):
            self.fire_cue(cue_index)

        return cb

    def request_selected_continuity(self, event=None):
        self.last_module_continuity_auto_ms = time.ticks_ms()

        uid = self.get_selected_module_uid_int()
        if uid is None:
            return

        cue_count = self.get_selected_module_cue_count()

        try:
            self.set_status("Running speed test...")
            ping_ms = nfcp.request_ping_for_uid(uid)
            self.save_selected_module_speed_test(ping_ms)
            self.refresh_selected_module_detail_labels(speed=True)

            self.set_status("Checking continuity...")
            ok = nfcp.request_continuity_for_uid(uid, cue_count)
            if ok is None:
                self.save_selected_module_online(False)
                self.refresh_selected_module_detail_labels(radio=True)
                self.set_status("Continuity timeout")
                return

            self.set_continuity(ok)
            self.save_selected_module_continuity(ok)
            self.update_open_script_cues()
            self.update_extra_connected_cues()
            self.refresh_selected_module_detail_labels(radio=True)
        except Exception as e:
            print("Continuity failed:", e)
            self.set_status("Continuity failed")

    def request_selected_speed_test(self):
        uid = self.get_selected_module_uid_int()
        if uid is None:
            return

        self.set_status("Running speed test...")

        try:
            ping_ms = nfcp.request_ping_for_uid(uid)
            self.save_selected_module_speed_test(ping_ms)

            if ping_ms is None:
                self.set_status("Speed test timeout")
            else:
                self.set_status("Speed test: %dms" % ping_ms)

            self.refresh_selected_module_detail_labels(speed=True, radio=True)

        except Exception as e:
            print("Speed test failed:", e)
            self.set_status("Speed test failed")

    def request_selected_info(self):
        uid = self.get_selected_module_uid_int()
        if uid is None:
            return

        self.set_status("Reading versions...")

        try:
            info = nfcp.request_info_for_uid(uid)
            if info:
                self.save_selected_module_info(info)
                self.set_status("Versions updated")
            else:
                self.set_status("Version read timeout")

            self.refresh_selected_module_detail_labels(version=True, radio=True)

        except Exception as e:
            print("Info read failed:", e)
            self.set_status("Version read failed")

    def reset_selected_uid_to_auto(self, event=None):
        if not self.selected_module:
            self.set_status("No module selected")
            return

        uid_text = self.selected_module.get("unique_id", "0x00000000")
        uid = self.get_selected_module_uid_int()
        if uid is None:
            return

        self.set_status("Resetting UID to hardware...")

        try:
            if not nfcp.request_set_unique_for_uid(uid, 0):
                self.set_status("UID reset failed")
                return
        except Exception as e:
            print("UID reset failed:", e)
            self.set_status("UID reset failed")
            return

        new_uid_text = self.selected_module.get("hardware_unique_id", "")
        if new_uid_text in ("", "0x00000000", "0"):
            self.load_modules_from_db()
            self.show_modules_menu()
            self.set_status("UID reset - rediscover module")
            return

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
            dev = idx.pop(uid_text, self.selected_module)
            dev["unique_id"] = new_uid_text
            dev["configured_unique_id"] = "0x00000000"
            idx[new_uid_text] = dev
            db["devices"] = sorted(
                idx.values(),
                key=lambda d: self.group_to_int_value(d.get("group_id", None))
            )
            nfcp.save_device_db(db)
            self.selected_module = dev
            self.load_modules_from_db()
        except Exception as e:
            print("UID DB update failed:", e)

        self.show_module_continuity()
        self.set_status("UID reset to hardware")

    def get_selected_module_uid_int(self):
        if not self.selected_module:
            self.set_status("No module selected")
            return None

        uid_text = self.selected_module.get("unique_id", "0x00000000")

        try:
            return int(uid_text, 16) & 0xFFFFFFFF
        except:
            self.set_status("Bad UID")
            return None

    def refresh_selected_module_detail_labels(
        self,
        speed=False,
        radio=False,
        version=False
    ):
        if speed and hasattr(self, "speed_label"):
            self.speed_label.set_text(self.format_selected_speed_test())

        if radio and hasattr(self, "radio_link_label"):
            self.radio_link_label.set_text(self.format_selected_radio_link())

        if version and hasattr(self, "version_label"):
            self.version_label.set_text(self.format_selected_versions())

        if version and hasattr(self, "identity_label"):
            self.identity_label.set_text(self.format_selected_identity())

    def apply_last_radio_link_to_module(self, dev, uid_text):
        link = nfcp.get_last_radio_link(uid_text)
        if not link:
            return

        if link.get("radio_rssi", None) is not None:
            dev["radio_rssi"] = link["radio_rssi"]
        if link.get("radio_snr", None) is not None:
            dev["radio_snr"] = link["radio_snr"]
        dev["radio_last_epoch"] = link.get("radio_last_epoch", time.time())

    def update_selected_module_record(self, label, updater):
        if not self.selected_module:
            return None

        uid_text = self.selected_module.get("unique_id", None)

        if not uid_text:
            return None

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
        except Exception as e:
            print("%s DB load failed:" % label, e)
            return None

        dev = idx.get(uid_text)

        if not dev:
            dev = self.selected_module

        updater(dev, uid_text)
        self.apply_last_radio_link_to_module(dev, uid_text)

        idx[uid_text] = dev
        self.selected_module = dev

        db["devices"] = sorted(
            idx.values(),
            key=lambda d: self.group_to_int_value(
                d.get("group_id", None)
            )
        )

        try:
            nfcp.save_device_db(db)
            self.load_modules_from_db()
            return dev
        except Exception as e:
            print("%s DB save failed:" % label, e)
            return None

    def save_selected_module_speed_test(self, ping_ms):
        def updater(dev, uid_text):
            dev["speed_test_target_uid"] = uid_text
            dev["speed_test_ms"] = ping_ms
            dev["speed_test_ok"] = ping_ms is not None
            dev["speed_test_last_epoch"] = time.time()
            dev["module_online"] = ping_ms is not None
            if ping_ms is None:
                dev["module_lost_epoch"] = time.time()
            else:
                dev["module_last_seen_epoch"] = time.time()

        self.update_selected_module_record("Speed test", updater)

    def save_selected_module_online(self, online):
        def updater(dev, uid_text):
            dev["module_online"] = bool(online)
            if online:
                dev["module_last_seen_epoch"] = time.time()
            else:
                dev["module_lost_epoch"] = time.time()

        self.update_selected_module_record("Module online", updater)

    def save_selected_module_info(self, info):
        def updater(dev, uid_text):
            dev["app_name"] = info.get("app_name", "")
            dev["app_version"] = info.get("app_version", "")
            dev["radio_firmware_version"] = info.get("radio_version", "")
            dev["script_status"] = info.get("script_status", "")
            dev["info_last_epoch"] = time.time()
            dev["module_online"] = True
            dev["module_last_seen_epoch"] = time.time()

        self.update_selected_module_record("Info", updater)

    def save_selected_module_continuity(self, ok):
        cue_count = self.get_selected_module_cue_count()
        cues = []

        for i in range(cue_count):
            cues.append({
                "cue": i + 1,
                "ok": bool(ok[i]) if i < len(ok) else False
            })

        def updater(dev, uid_text):
            dev["cue_count"] = cue_count
            dev["continuity"] = {"cues": cues}
            dev["continuity_last_epoch"] = time.time()
            dev["module_online"] = True
            dev["module_last_seen_epoch"] = time.time()

        self.update_selected_module_record("Continuity", updater)

    def set_continuity(self, continuity):
        cue_count = self.get_selected_module_cue_count()

        self.continuity = [False] * cue_count

        for i in range(cue_count):
            self.continuity[i] = bool(continuity[i]) if i < len(continuity) else False

        self.refresh_cues()

    def selected_cue_is_in_script(self, cue_number):
        if not self.selected_script:
            return True

        if not self.selected_module:
            return True

        group_text = self.selected_module.get("group_id", None)

        group_id = self.parse_group_id(group_text)
        if group_id is None:
            return False

        for event in self.loaded_script_pyro_events:
            try:
                ch = int(event.get("channel"))
                cue = int(event.get("cue"))

                if ch == group_id and cue == cue_number:
                    return True

            except:
                pass

        return False

    def refresh_cues(self):
        if not hasattr(self, "cue_labels"):
            return

        cue_count = self.get_selected_module_cue_count()
        good = 0

        for i in range(cue_count):
            cue_number = i + 1
            has_cont = self.continuity[i] if i < len(self.continuity) else False
            cue_in_script = self.selected_cue_is_in_script(cue_number)

            if has_cont:
                good += 1

                if cue_in_script:
                    text = "Cue %02d: GOOD" % cue_number
                    color = lv.color_hex(0x1F7A1F)
                else:
                    text = "Cue %02d: EXTRA" % cue_number
                    color = lv.color_hex(0xC46A00)

            else:
                if cue_in_script:
                    text = "Cue %02d: OPEN" % cue_number
                    color = lv.color_hex(0x7A1F1F)
                else:
                    text = "Cue %02d: UNUSED" % cue_number
                    color = lv.color_hex(0x4A4A4A)

            self.cue_labels[i].set_text(text)
            self.cue_labels[i].center()

            self.cue_buttons[i].set_style_bg_color(color, 0)

        if hasattr(self, "summary"):
            self.summary.set_text("Continuity: %d / %d" % (good, cue_count))

        self.set_status("Continuity updated")

    # -------------------------
    # Change Channel
    # -------------------------

    def show_change_group(self, event=None):
        if not self.selected_module:
            self.set_status("No module selected")
            return

        scr = self.new_screen("Change Channel", "change_channel")

        group_text = self.selected_module.get("group_id", "0x0001")

        current_group = self.parse_group_id(group_text, 1)

        self.group_edit_value = current_group
        self.group_edit_text = str(current_group)

        self.make_button(scr, "BACK", 20, 400, 120, 55, self.show_module_continuity)
        self.make_button(scr, "SAVE", 650, 400, 120, 55, self.save_group_change)

        channel_title = lv.label(scr)
        channel_title.set_text("Channel")
        channel_title.align(lv.ALIGN.TOP_MID, 0, 70)

        self.group_edit_box = lv.obj(scr)
        self.group_edit_box.set_size(260, 70)
        self.group_edit_box.align(lv.ALIGN.TOP_MID, 0, 95)
        self.group_edit_box.set_style_bg_color(lv.color_hex(0x202020), 0)
        self.group_edit_box.set_style_border_color(lv.color_hex(0xFFFFFF), 0)
        self.group_edit_box.set_style_border_width(2, 0)
        self.group_edit_box.set_style_radius(8, 0)
        self.group_edit_box.set_style_pad_all(0, 0)

        self.group_edit_label = lv.label(self.group_edit_box)
        self.group_edit_label.set_text(self.group_edit_text)
        self.group_edit_label.center()

        self.build_group_number_pad(scr)

    def build_group_number_pad(self, parent):
        x0 = 190
        y0 = 185

        btn_w = 120
        btn_h = 48
        gap = 14

        keys = [
            ["1", "2", "3"],
            ["4", "5", "6"],
            ["7", "8", "9"],
            ["CLR", "0", "DEL"]
        ]

        for row in range(4):
            for col in range(3):
                key = keys[row][col]

                x = x0 + col * (btn_w + gap)
                y = y0 + row * (btn_h + gap)

                btn = lv.button(parent)
                btn.set_pos(x, y)
                btn.set_size(btn_w, btn_h)

                lbl = lv.label(btn)
                lbl.set_text(key)
                lbl.center()

                btn.add_event_cb(
                    self.make_group_key_cb(key),
                    lv.EVENT.CLICKED,
                    None
                )

    def make_group_key_cb(self, key):
        def cb(event):
            self.group_key_pressed(key)

        return cb

    def group_key_pressed(self, key):
        if key == "CLR":
            self.group_edit_text = ""

        elif key == "DEL":
            if len(self.group_edit_text) > 0:
                self.group_edit_text = self.group_edit_text[:-1]

        else:
            if len(self.group_edit_text) < 5:
                if self.group_edit_text == "0":
                    self.group_edit_text = key
                else:
                    self.group_edit_text += key

        if self.group_edit_text == "":
            self.group_edit_value = 0
        else:
            try:
                self.group_edit_value = int(self.group_edit_text)

                if self.group_edit_value > 0xFFFF:
                    self.group_edit_value = 0xFFFF
                    self.group_edit_text = str(self.group_edit_value)

            except:
                self.group_edit_value = 0
                self.group_edit_text = ""

        self.refresh_group_edit_label()

    def refresh_group_edit_label(self):
        if hasattr(self, "group_edit_label"):
            if self.group_edit_text == "":
                self.group_edit_label.set_text("_")
            else:
                self.group_edit_label.set_text(self.group_edit_text)

            self.group_edit_label.center()

    def save_group_change(self, event=None):
        if not self.selected_module:
            self.set_status("No module selected")
            return

        if self.group_edit_value < 0 or self.group_edit_value > 0xFFFF:
            self.set_status("Invalid channel")
            return

        uid_text = self.selected_module.get("unique_id", "0x00000000")

        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            self.set_status("Bad UID")
            return

        new_group = self.group_edit_value & 0xFFFF

        try:
            nfcp.set_group(uid, new_group)
        except Exception as e:
            print("Set channel failed:", e)
            self.set_status("Set channel failed")
            return

        new_group_text = "0x%04X" % new_group

        self.selected_module["group_id"] = new_group_text

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)

            dev = idx.get(uid_text)

            if dev:
                dev["group_id"] = new_group_text
                idx[uid_text] = dev

                db["devices"] = sorted(
                    idx.values(),
                    key=lambda d: self.group_to_int_value(
                        d.get("group_id", None)
                    )
                )

                nfcp.save_device_db(db)

            self.load_modules_from_db()
            self.update_script_module_mismatch()
            self.update_open_script_cues()
            self.update_extra_connected_cues()

        except Exception as e:
            print("DB update after set channel failed:", e)

        self.set_status("Channel changed to %d" % new_group)

        self.show_module_continuity()

    # -------------------------
    # Fire / Arm
    # -------------------------

    def fire_cue(self, cue_index):
        if not self.armed:
            self.set_status("Fire blocked - not armed")
            return

        if not self.selected_module:
            self.set_status("No module selected")
            return

        uid_text = self.selected_module.get("unique_id", "0x00000000")

        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            self.set_status("Bad UID")
            return

        try:
            nfcp.fire_unique(uid, cue_index)
            self.set_status("Cue %d fired" % (cue_index + 1))
        except Exception as e:
            print("Fire failed:", e)
            self.set_status(
                "Fire failed: %s %s" % (
                    e.__class__.__name__,
                    str(e)
                )
            )

    def toggle_arm(self, event=None):
        if not self.armed and not self.has_scanned_modules():
            self.set_status("Arm disabled - scan modules first")
            return

        self.set_armed(not self.armed)

    def set_armed(self, armed):
        if armed and not self.has_scanned_modules():
            self.set_status("Arm disabled - scan modules first")
            return

        target_armed = bool(armed)
        ack_failed = 0
        module_uids = self.get_module_uids()

        if module_uids:
            self.set_status("Arming modules..." if target_armed else "Disarming modules...")

        for uid in module_uids:
            try:
                if target_armed:
                    ok = nfcp.arm_for_uid(uid)
                else:
                    ok = nfcp.disarm_for_uid(uid)
                if not ok:
                    ack_failed += 1
            except Exception as e:
                ack_failed += 1
                print("Unique arm/disarm failed:", e)

        if module_uids and ack_failed:
            self.set_status("Arm ACK failed for %d module(s)" % ack_failed)
            return

        if not module_uids:
            try:
                if target_armed:
                    nfcp.arm()
                else:
                    nfcp.disarm()
            except Exception as e:
                print("Arm/disarm failed:", e)
                self.set_status("Arm/disarm failed")
                return

        self.armed = target_armed

        if hasattr(self, "arm_label"):
            try:
                self.arm_label.set_text("ARM: ON" if self.armed else "ARM: OFF")
            except Exception as e:
                print("Arm label update failed:", e)

        if hasattr(self, "arm_btn_label"):
            try:
                self.arm_btn_label.set_text("DISARM" if self.armed else "ARM")
                self.arm_btn_label.center()
            except Exception as e:
                print("Arm button label update failed:", e)

        self.update_background()
        self.update_manual_fire_button()
        self.update_script_fire_button()
        self.update_arm_button()

    # -------------------------
    # UART packet monitor
    # -------------------------

    def poll_nfcp_responses(self):
        try:
            packets = nfcp.poll_responses()
        except Exception as e:
            print("NFCP poll failed:", e)
            return

        db_changed = False
        new_module_added = False

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
        except Exception as e:
            print("DB load/index failed:", e)
            db = {"generated_epoch": None, "device_count": 0, "devices": []}
            idx = {}

        for pkt in packets:
            src = pkt.get("src_unique", 0)
            cmd = pkt.get("cmd", 0)
            data = pkt.get("data", b"")

            print(
                "NFCP RX src=0x%08X cmd=0x%02X data=%s" %
                (
                    src,
                    cmd,
                    " ".join("%02X" % b for b in data)
                )
            )

            if cmd == nfcp.CMD_ACK:
                self.set_status("ACK from 0x%08X" % src)

            elif cmd == nfcp.CMD_NACK:
                self.set_status("NACK from 0x%08X" % src)

            elif cmd == nfcp.CMD_DISCOVER_RSP:
                info = nfcp.parse_discover_rsp(src, data)

                if info:
                    uid = info["unique_id"]
                    was_new = uid not in idx

                    existing = idx.get(uid, {})
                    existing.update(info)
                    existing["module_online"] = True
                    existing["module_last_seen_epoch"] = time.time()
                    idx[uid] = existing
                    db_changed = True

                    if was_new:
                        new_module_added = True
                        self.set_status("Added module %s" % uid)
                    else:
                        self.set_status("Updated module %s" % uid)

                    print("Added/updated module:", info)

            elif cmd == nfcp.CMD_CONTINUITY_RSP:
                self.set_status("Continuity response from 0x%08X" % src)

        if db_changed:
            db["devices"] = sorted(
                idx.values(),
                key=lambda d: self.group_to_int_value(
                    d.get("group_id", None)
                )
            )

            try:
                nfcp.save_device_db(db)
                self.load_modules_from_db()
                self.update_script_module_mismatch()
                self.update_open_script_cues()
                self.update_extra_connected_cues()
                self.update_arm_button()

                if new_module_added and self.current_view == "modules":
                    self.show_modules_menu()

            except Exception as e:
                print("DB save failed:", e)

    # -------------------------
    # Runtime
    # -------------------------

    def set_status(self, text):
        if hasattr(self, "status"):
            self.status.set_text("Status: " + text)

        print(text)

    def script_fire_timing_active(self):
        return (
            self.current_view == "script_fire" and
            self.script_fire_mode == "auto" and
            self.script_fire_running
        )

    def trellis_polling_active(self):
        return self.current_view == "manual_fire" or self.current_view == "script_fire"

    def loop_sleep_ms(self):
        if self.script_fire_timing_active():
            return 1

        if self.trellis_polling_active():
            return 1

        return 10

    def lvgl_handler_due(self):
        if self.script_fire_timing_active() or self.trellis_polling_active():
            return time.ticks_diff(time.ticks_ms(), self.last_lvgl_handler_ms) >= 20

        return True

    def update(self):
        if self.current_view == "manual_fire":
            self.poll_manual_fire_trellis()
            self.update_manual_fire_last_indicator()

        if self.current_view == "script_fire":
            self.update_script_fire_clock()
            self.poll_script_fire_auto()
            self.poll_script_fire_trellis()
            self.update_pending_script_fire_step_status()

        if (
            self.auto_module_continuity and
            self.current_view == "module_continuity" and
            self.selected_module
        ):
            now = time.ticks_ms()
            elapsed = time.ticks_diff(now, self.last_module_continuity_auto_ms)

            if elapsed >= MODULE_CONTINUITY_AUTO_CHECK_MS:
                self.request_selected_continuity()


delete_device_db_on_startup()

gui = NFCPGui()


while True:
    gui.update()

    if not gui.script_fire_timing_active():
        gui.poll_nfcp_responses()

    if gui.lvgl_handler_due():
        try:
            lv.timer_handler()
            gui.last_lvgl_handler_ms = time.ticks_ms()
        except Exception:
            pass

    time.sleep_ms(gui.loop_sleep_ms())
