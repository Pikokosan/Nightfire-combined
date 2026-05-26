# main.py
# NFCP Master Controller GUI
# Waveshare ESP32-S3-LCD-4.3
# LVGL 9.2.2
# Uses shared nfcp.py for packet protocol

from machine import UART, Pin
import time
import os
import lvgl as lv
import display_driver
import ujson as json
import urandom as random
import nfcp

DEFAULT_CUE_COUNT = 16
MAX_CUE_COUNT = 64
MODULE_CONTINUITY_AUTO_CHECK_MS = 4000
DISCOVER_IDLE_TIMEOUT_MS = 3000
DISCOVER_MAX_SCAN_MS = 30000
SPEED_TEST_TIMEOUT_MS = 2000

SRC_ID = 0x01
DEVICE_DB_PATH = "nfcp_devices.json"
SETTINGS_PATH = "nfcp_settings.json"

UART_ID = 1
BAUDRATE = 256000
TX_PIN = 16
RX_PIN = 15
RS485_DE_RE_PIN = 6

uart = UART(
    UART_ID,
    baudrate=BAUDRATE,
    tx=Pin(TX_PIN),
    rx=Pin(RX_PIN)
)

if RS485_DE_RE_PIN is not None:
    de = Pin(RS485_DE_RE_PIN, Pin.OUT)
    de.value(0)
else:
    de = None

nfcp_tx = nfcp.NFCP(SRC_ID)
nfcp_rx = nfcp.NFCPStreamParser()
_last_packets = []

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


def delete_device_db_on_startup():
    try:
        os.remove("nfcp_devices.json")
        print("Deleted nfcp_devices.json on startup")
    except OSError:
        print("No nfcp_devices.json to delete")
    except Exception as e:
        print("Failed to delete nfcp_devices.json:", e)


def rs485_write(pkt):
    if de is not None:
        de.value(1)
        time.sleep_us(500)

    uart.write(pkt)

    bits = len(pkt) * 10
    tx_time_us = int((bits * 1000000) // BAUDRATE)
    time.sleep_us(tx_time_us + 3000)

    if de is not None:
        de.value(0)
        time.sleep_us(500)


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

    return {
        "auto_module_continuity": bool(settings.get("auto_module_continuity", True)),
        "color_cues_without_script": bool(settings.get("color_cues_without_script", True))
    }


def save_app_settings(settings, path=SETTINGS_PATH):
    with open(path, "w") as f:
        json.dump(settings, f)


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


def poll_responses(max_packets=8):
    global _last_packets

    packets = []
    data = uart_read_some(256)
    if not data:
        return packets

    for b in data:
        pkt = nfcp_rx.feed(b)
        if not pkt:
            continue

        if pkt.get("frame_type") != nfcp.SOF_RESPONSE:
            continue

        out = {
            "sof2": nfcp.SOF2_RESPONSE,
            "src_unique": pkt.get("src", 0),
            "cmd": pkt.get("cmd", 0),
            "data": pkt.get("data", b"")
        }
        packets.append(out)
        _last_packets.append(out)

        if len(_last_packets) > 20:
            _last_packets = _last_packets[-20:]
        if len(packets) >= max_packets:
            break

    return packets


def get_last_packets():
    return _last_packets


def discover(idle_ms=DISCOVER_IDLE_TIMEOUT_MS, max_scan_ms=DISCOVER_MAX_SCAN_MS):
    drain_uart()

    nonce = random.getrandbits(16)
    rs485_write(nfcp_tx.build_discover_broadcast(nonce))

    db = load_device_db()
    idx = index_db_by_unique(db)
    start_ms = time.ticks_ms()
    last_packet_ms = start_ms
    max_deadline = time.ticks_add(start_ms, max_scan_ms)

    while time.ticks_diff(max_deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_DISCOVER_RSP:
                continue

            info = parse_discover_rsp(pkt["src_unique"], pkt["data"])
            if info:
                idx[info["unique_id"]] = info
                last_packet_ms = time.ticks_ms()

        if time.ticks_diff(time.ticks_ms(), last_packet_ms) >= idle_ms:
            break

        time.sleep_ms(1)

    db["devices"] = sorted(idx.values(), key=lambda d: d.get("unique_id", ""))
    save_device_db(db)
    return db["devices"]


def request_continuity_for_uid(uid_int, cue_count_hint=16, timeout_ms=300):
    drain_uart()
    rs485_write(nfcp_tx.build_continuity_request(uid_int))

    deadline = time.ticks_add(time.ticks_ms(), timeout_ms)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        packets = poll_responses()
        for pkt in packets:
            if pkt["cmd"] != nfcp.CMD_CONTINUITY_RSP:
                continue
            if (pkt["src_unique"] & 0xFFFFFFFF) != (uid_int & 0xFFFFFFFF):
                continue
            return parse_continuity_rsp(pkt["data"], cue_count_hint)

        time.sleep_ms(2)

    try:
        cue_count = int(cue_count_hint or DEFAULT_CUE_COUNT)
    except:
        cue_count = DEFAULT_CUE_COUNT
    return [False] * cue_count


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
    rs485_write(nfcp_tx.build_ping(uid_int, payload))

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

            return time.ticks_diff(time.ticks_ms(), start)

        time.sleep_ms(2)

    return None


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
        cues = []
        for i in range(cue_count):
            cues.append({
                "cue": i + 1,
                "ok": bool(ok[i]) if i < len(ok) else False
            })

        dev["continuity"] = {"cues": cues}
        dev["continuity_last_epoch"] = time.time()
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

        results.append({
            "unique_id": uid_text,
            "ping_ms": ping_ms
        })
        changed = True

    if changed:
        save_device_db(db)

    return results


def arm():
    rs485_write(nfcp_tx.build_arm_broadcast())


def disarm():
    rs485_write(nfcp_tx.build_disarm_broadcast())


def fire_unique(uid_int, cue_index_0based):
    if cue_index_0based < 0 or cue_index_0based >= MAX_CUE_COUNT:
        raise ValueError("cue index out of range")

    rs485_write(nfcp_tx.build_unique_fire(uid_int, 1 << cue_index_0based))


def set_group(uid_int, new_group):
    rs485_write(nfcp_tx.build_set_group(uid_int, new_group))


nfcp.load_device_db = load_device_db
nfcp.save_device_db = save_device_db
nfcp.clear_device_db = clear_device_db
nfcp.index_db_by_unique = index_db_by_unique
nfcp.parse_discover_rsp = parse_discover_rsp
nfcp.parse_continuity_rsp = parse_continuity_rsp
nfcp.poll_responses = poll_responses
nfcp.get_last_packets = get_last_packets
nfcp.discover = discover
nfcp.request_continuity_for_uid = request_continuity_for_uid
nfcp.continuity_scan = continuity_scan
nfcp.request_ping_for_uid = request_ping_for_uid
nfcp.speed_test_scan = speed_test_scan
nfcp.arm = arm
nfcp.disarm = disarm
nfcp.fire_unique = fire_unique
nfcp.set_group = set_group


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
        self.last_module_continuity_auto_ms = time.ticks_ms()
        self.settings = load_app_settings()
        self.auto_module_continuity = bool(
            self.settings.get("auto_module_continuity", True)
        )
        self.color_cues_without_script = bool(
            self.settings.get("color_cues_without_script", True)
        )

        self.group_edit_value = 1
        self.group_edit_text = ""

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
        self.open_script_cue_count = 0
        self.extra_connected_cue_count = 0

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
            "arm_btn",
            "discover_btn",
            "refresh_btn",
            "module_list",
            "module_label",
            "arm_label",
            "speed_label",
            "summary",
            "grid_box",
            "cue_buttons",
            "cue_labels",
            "auto_continuity_checkbox",
            "no_script_color_checkbox",
            "group_edit_box",
            "group_edit_label",
        )

        for name in names:
            try:
                if hasattr(self, name):
                    delattr(self, name)
            except Exception:
                pass

    def new_screen(self, title_text, view_name=None):
        old_screen = self.current_screen
        self.clear_screen_refs()
        scr = lv.obj()

        scr.set_style_bg_color(
            lv.color_hex(0x400000) if self.armed else lv.color_hex(0x101010),
            0
        )

        scr.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        load_screen(scr)
        self.delete_old_screen(old_screen)

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

        self.make_button(scr, "VIEW SCRIPT", 60, 90, 300, 80, self.show_view_script)
        self.make_button(scr, "LOAD SCRIPT", 440, 90, 300, 80, self.show_load_script)

        self.make_button(scr, "MODULES", 60, 200, 300, 80, self.show_modules_menu)
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

        self.arm_btn = self.make_button(
            scr,
            "DISARM" if self.armed else "ARM",
            440,
            315,
            300,
            80,
            self.toggle_arm
        )

        self.update_manual_fire_button()
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

    def has_scanned_modules(self):
        return len(self.modules) > 0

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

        lower = filename.lower()

        if lower == "cobra.csv":
            self.load_cobra_script(filename)
        else:
            self.set_status("Selected script: " + filename)

        print("Selected script:", filename)
        self.show_view_script()

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

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)

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

        scr = self.new_screen("Manual Fire", "manual_fire")

        label = lv.label(scr)
        label.set_text("Manual fire screen not loaded yet.")
        label.align(lv.ALIGN.CENTER, 0, 0)

        self.make_button(scr, "BACK", 20, 400, 140, 55, self.show_main_menu)

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

            text = "Ch %s  Cues:%s\nUID:%s" % (
                channel_text,
                cue_count,
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
            lbl.align(lv.ALIGN.LEFT_MID, 8, 0)

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

        self.load_modules_from_db()
        self.update_script_module_mismatch()
        self.update_open_script_cues()
        self.update_extra_connected_cues()

        count = len(results) if results else 0
        speed_count = len(speed_results) if speed_results else 0
        print("Continuity checked:", count)
        print("Speed tested:", speed_count)

        self.show_modules_menu()
        self.set_status("Refresh complete - %d checked, %d speed tested" % (count, speed_count))

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

        try:
            self.modules = nfcp.discover()
            self.load_modules_from_db()

            self.show_loading_screen("Discovering modules...\nChecking continuity...")
            cont_results = nfcp.continuity_scan()
            cont_count = len(cont_results) if cont_results else 0

            self.show_loading_screen("Discovering modules...\nRunning speed tests...")
            speed_results = nfcp.speed_test_scan()
            speed_count = len(speed_results) if speed_results else 0

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
                "Discovery complete - %d checked, %d speed tested" %
                (cont_count, speed_count)
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

        self.show_loading_screen("Opening module...\nRunning speed and continuity checks...")
        self.request_selected_speed_test()
        self.request_selected_continuity()
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
        self.speed_label.set_pos(500, 82)

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

            btn.add_event_cb(
                self.make_fire_cb(i),
                lv.EVENT.CLICKED,
                None
            )

            self.cue_buttons.append(btn)
            self.cue_labels.append(lbl)

    def make_fire_cb(self, cue_index):
        def cb(event):
            self.fire_cue(cue_index)

        return cb

    def request_selected_continuity(self, event=None):
        if not self.selected_module:
            self.set_status("No module selected")
            return

        self.last_module_continuity_auto_ms = time.ticks_ms()

        uid_text = self.selected_module.get("unique_id", "0x00000000")

        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            self.set_status("Bad UID")
            return

        cue_count = self.get_selected_module_cue_count()

        self.set_status("Checking continuity...")

        try:
            ok = nfcp.request_continuity_for_uid(uid, cue_count)
            self.set_continuity(ok)
            self.save_selected_module_continuity(ok)
            self.update_open_script_cues()
            self.update_extra_connected_cues()
        except Exception as e:
            print("Continuity failed:", e)
            self.set_status("Continuity failed")

    def request_selected_speed_test(self):
        if not self.selected_module:
            self.set_status("No module selected")
            return

        uid_text = self.selected_module.get("unique_id", "0x00000000")

        try:
            uid = int(uid_text, 16) & 0xFFFFFFFF
        except:
            self.set_status("Bad UID")
            return

        self.set_status("Running speed test...")

        try:
            ping_ms = nfcp.request_ping_for_uid(uid)
            self.save_selected_module_speed_test(ping_ms)

            if ping_ms is None:
                self.set_status("Speed test timeout")
            else:
                self.set_status("Speed test: %dms" % ping_ms)

            if hasattr(self, "speed_label"):
                self.speed_label.set_text(self.format_selected_speed_test())

        except Exception as e:
            print("Speed test failed:", e)
            self.set_status("Speed test failed")

    def save_selected_module_speed_test(self, ping_ms):
        if not self.selected_module:
            return

        uid_text = self.selected_module.get("unique_id", None)

        if not uid_text:
            return

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
        except Exception as e:
            print("Speed test DB load failed:", e)
            return

        dev = idx.get(uid_text)

        if not dev:
            dev = self.selected_module

        dev["speed_test_target_uid"] = uid_text
        dev["speed_test_ms"] = ping_ms
        dev["speed_test_ok"] = ping_ms is not None
        dev["speed_test_last_epoch"] = time.time()

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
        except Exception as e:
            print("Speed test DB save failed:", e)

    def save_selected_module_continuity(self, ok):
        if not self.selected_module:
            return

        uid_text = self.selected_module.get("unique_id", None)

        if not uid_text:
            return

        try:
            db = nfcp.load_device_db()
            idx = nfcp.index_db_by_unique(db)
        except Exception as e:
            print("Continuity DB load failed:", e)
            return

        dev = idx.get(uid_text)

        if not dev:
            dev = self.selected_module

        cue_count = self.get_selected_module_cue_count()

        cues = []

        for i in range(cue_count):
            cues.append({
                "cue": i + 1,
                "ok": bool(ok[i]) if i < len(ok) else False
            })

        dev["cue_count"] = cue_count
        dev["continuity"] = {"cues": cues}
        dev["continuity_last_epoch"] = time.time()

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
        except Exception as e:
            print("Continuity DB save failed:", e)

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

        self.armed = bool(armed)

        try:
            if self.armed:
                nfcp.arm()
            else:
                nfcp.disarm()
        except Exception as e:
            print("Arm/disarm failed:", e)
            self.set_status("Arm/disarm failed")

        if hasattr(self, "arm_label"):
            try:
                self.arm_label.set_text("ARM: ON" if self.armed else "ARM: OFF")
            except Exception as e:
                print("Arm label update failed:", e)

        self.update_background()
        self.update_manual_fire_button()
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

                    idx[uid] = info
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

    def update(self):
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
    gui.poll_nfcp_responses()

    try:
        lv.timer_handler()
    except Exception:
        pass

    time.sleep_ms(10)
