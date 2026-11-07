# 16x2 LCD menu shared by the field and dummy module firmware.
#
# The firmware passes a "node" object. LcdMenu calls:
#   node.ids()                      -> (network_id, group_id, unique_id, channel)
#   node.armed()                    -> bool
#   node.status_text()              -> "SAFE", "ARMED" or "FAULT"
#   node.battery_mv()               -> int
#   node.cue_count()                -> int
#   node.firmware_version           -> str
#   node.continuity()               -> (open_mask or None, status word)
#   node.can_scan                   -> True if OK in the continuity view scans
#   node.scan_continuity()          -> runs a local continuity scan
#   node.apply_setting(name, value) -> bool; must save it
#   node.alert_lines()              -> None, or two lines that override every page
#
# Keep every top-level name here unique: the field image bundles this file
# into one namespace with field_main.py.

import time

import nrf_module_common
import nrf_radio
from module_link import count_mask_bits

try:
    from lcd1602_i2c import I2cLcd1602
except Exception:
    I2cLcd1602 = None

LCD_MENU_ITEMS = (
    "System Info",
    "Continuity",
    "Script",
    "Set Group",
    "Set Network",
    "Set Channel",
)


class DebouncedButton:
    def __init__(self, pin, debounce_ms=80, active_value=0):
        self.pin = pin
        self.debounce_ms = int(debounce_ms)
        self.active_value = active_value
        self.last_raw = self.read_raw()
        self.stable = self.last_raw
        self.changed_ms = time.ticks_ms()

    def read_raw(self):
        if self.pin is None:
            return False
        try:
            return self.pin.value() == self.active_value
        except Exception:
            return False

    def update(self, now):
        """Returns True once per debounced press."""
        raw = self.read_raw()
        if raw != self.last_raw:
            self.last_raw = raw
            self.changed_ms = now
            return False

        if raw != self.stable and time.ticks_diff(now, self.changed_ms) >= self.debounce_ms:
            self.stable = raw
            return self.stable

        return False


class LcdMenu:
    def __init__(self, node, link, script, cols=16, rows=2, refresh_ms=350, page_size=8, link_timeout_ms=15000):
        self.node = node
        self.link = link
        self.script = script
        self.cols = cols
        self.rows = rows
        self.refresh_ms = refresh_ms
        self.page_size = max(1, int(page_size or 1))
        self.link_timeout_ms = link_timeout_ms
        self.display = None
        self.buttons = []
        self.mode = "home"
        self.menu_index = 0
        self.active_view = ""
        self.cont_detail_index = -1
        self.edit_target = ""
        self.edit_value = 0
        self.force_refresh = True
        self.last_refresh_ms = 0
        self.scan_pending = False

    def open(self, i2c, address, mapping, title, buttons):
        """Finds the LCD on i2c and shows the boot screen. buttons: [(name, DebouncedButton)]."""
        if I2cLcd1602 is None:
            print("LCD driver unavailable")
            return
        try:
            found = I2cLcd1602.find_address(i2c, address)
            if found is None:
                print("LCD not found on I2C")
                return
            self.display = I2cLcd1602(i2c, found, self.cols, self.rows, mapping=mapping)
            self.display.show_lines(title, "Booting...")
            self.buttons = buttons
            self.force_refresh = True
            print("LCD ready at 0x%02X mapping=%s" % (found, mapping))
        except Exception as e:
            self.display = None
            print("LCD init failed:", e)

    def refresh(self):
        self.force_refresh = True

    def show_now(self, line1, line2):
        if self.display:
            try:
                self.display.show_lines(line1, line2)
            except Exception:
                pass

    # --- navigation --------------------------------------------------------
    def detail_pages(self):
        return max(1, (self.node.cue_count() + self.page_size - 1) // self.page_size)

    def start_edit(self, target, value):
        self.mode = "edit"
        self.edit_target = target
        self.edit_value = int(value or 0)

    def open_menu_item(self):
        item = LCD_MENU_ITEMS[self.menu_index]
        ids = self.node.ids()
        if item == "System Info":
            self.mode = "view"
            self.active_view = "system"
        elif item == "Continuity":
            self.mode = "view"
            self.active_view = "continuity"
            self.cont_detail_index = -1
        elif item == "Script":
            self.mode = "view"
            self.active_view = "script"
        elif item == "Set Group":
            self.start_edit("group", ids[1])
        elif item == "Set Network":
            self.start_edit("network", ids[0])
        elif item == "Set Channel":
            self.start_edit("channel", ids[3])

    def adjust_edit(self, delta):
        if self.edit_target == "channel":
            self.edit_value = (self.edit_value + delta) % 101
            while not nrf_module_common.valid_radio_channel(self.edit_value):
                self.edit_value = (self.edit_value + delta) % 101
        else:
            self.edit_value = (self.edit_value + delta) & 0xFFFF

    def blocked(self):
        self.mode = "view"
        self.active_view = "edit_blocked"

    def save_edit(self):
        if self.node.armed():
            self.blocked()
            return

        if self.edit_target == "channel":
            if not nrf_module_common.valid_radio_channel(self.edit_value):
                self.blocked()
                return
            value = self.edit_value % 101
        else:
            value = self.edit_value & 0xFFFF

        if not self.node.apply_setting(self.edit_target, value):
            self.blocked()
            return

        if self.edit_target == "channel":
            try:
                nrf_radio.init(
                    channel=self.node.ids()[3],
                    tx_power=self.link.tx_power,
                    address=self.link.address,
                )
            except Exception as e:
                print("LCD channel radio init failed:", e)

        self.mode = "menu"

    def button(self, name):
        if self.mode == "home":
            if name == "ok":
                self.mode = "menu"
        elif self.mode == "menu":
            if name == "up":
                self.menu_index = (self.menu_index - 1) % len(LCD_MENU_ITEMS)
            elif name == "down":
                self.menu_index = (self.menu_index + 1) % len(LCD_MENU_ITEMS)
            elif name == "back":
                self.mode = "home"
            elif name == "ok":
                self.open_menu_item()
        elif self.mode == "view":
            continuity = self.active_view == "continuity"
            if name == "back":
                self.mode = "menu"
            elif continuity and name == "ok" and self.node.can_scan:
                self.scan_pending = True
            elif continuity and name == "up":
                self.cont_detail_index -= 1
                if self.cont_detail_index < -1:
                    self.cont_detail_index = self.detail_pages() - 1
            elif continuity and name == "down":
                self.cont_detail_index += 1
                if self.cont_detail_index >= self.detail_pages():
                    self.cont_detail_index = -1
            elif name == "ok":
                self.mode = "menu"
        elif self.mode == "edit":
            if name == "up":
                self.adjust_edit(1)
            elif name == "down":
                self.adjust_edit(-1)
            elif name == "back":
                self.mode = "menu"
            elif name == "ok":
                self.save_edit()
        self.force_refresh = True

    # --- pages -------------------------------------------------------------
    def home_lines(self):
        mv = int(self.node.battery_mv() or 0)
        left = "%d.%02dV" % (mv // 1000, (mv % 1000) // 10)
        ids = self.node.ids()
        right = "G%04X" % (ids[1] & 0xFFFF)
        gap = self.cols - len(left) - len(right)
        if gap < 1:
            gap = 1
        link_text = "M:OK" if self.link.master_link_ok(self.link_timeout_ms) else "M:--"
        return left + (" " * gap) + right, "%s %08X" % (link_text, ids[2] & 0xFFFFFFFF)

    def edit_lines(self):
        if self.edit_target == "channel":
            return "Channel", "%03d OK=save" % self.edit_value
        if self.edit_target == "network":
            return "Network ID", "%04X OK=save" % (self.edit_value & 0xFFFF)
        return "Group ID", "%04X OK=save" % (self.edit_value & 0xFFFF)

    def continuity_lines(self):
        cue_count = self.node.cue_count()
        open_mask, status = self.node.continuity()
        if self.cont_detail_index < 0:
            if open_mask is None:
                return "Cont not checked", "OK=Scan"
            good = cue_count - count_mask_bits(open_mask)
            return (
                "Cont %02d/%02d %s" % (good, cue_count, status),
                "OK=Scan UP/DN" if self.node.can_scan else "UP/DN Details",
            )

        start = self.cont_detail_index * self.page_size + 1
        end = min(cue_count, start + self.page_size - 1)
        if open_mask is None:
            return "Cues %02d-%02d" % (start, end), "-- no data --"

        parts = []
        for cue in range(start, end + 1):
            open_bit = bool(open_mask & (1 << (cue - 1)))
            parts.append("%02d%s" % (cue, " " if open_bit else chr(255)))
        return " ".join(parts[:4]), " ".join(parts[4:8])

    def page_lines(self):
        alert = self.node.alert_lines()
        if alert:
            return alert
        if self.mode == "home":
            return self.home_lines()
        if self.mode == "menu":
            item = LCD_MENU_ITEMS[self.menu_index]
            next_item = LCD_MENU_ITEMS[(self.menu_index + 1) % len(LCD_MENU_ITEMS)]
            return ">%s" % item, " %s" % next_item
        if self.mode == "edit":
            return self.edit_lines()
        if self.active_view == "system":
            ids = self.node.ids()
            return (
                "FW %s C%02d" % (self.node.firmware_version, self.node.cue_count()),
                "Net%04X Ch%03d" % (ids[0] & 0xFFFF, ids[3] & 0xFF),
            )
        if self.active_view == "script":
            script = self.script
            script_id_text = "----" if script.id_loaded is None else "%04X" % script.id_loaded
            return (
                "Script %s" % script_id_text,
                "%s Run%d N%03d" % (self.node.status_text(), 1 if script.running else 0, script.next_index),
            )
        if self.active_view == "continuity":
            return self.continuity_lines()
        if self.active_view == "edit_blocked":
            return "Disarm first", "BACK=menu"
        return self.home_lines()

    def service(self):
        """Reads buttons and redraws. Call this from the main loop."""
        if self.display is None:
            return
        try:
            now = time.ticks_ms()
            for name, button in self.buttons:
                if button.update(now):
                    self.button(name)

            if self.scan_pending:
                self.scan_pending = False
                self.show_now("Checking cues", "Please wait...")
                self.node.scan_continuity()

            if self.force_refresh or time.ticks_diff(now, self.last_refresh_ms) >= self.refresh_ms:
                line1, line2 = self.page_lines()
                self.display.show_lines(str(line1)[:self.cols], str(line2)[:self.cols])
                self.last_refresh_ms = now
                self.force_refresh = False
        except Exception as e:
            self.display = None
            print("LCD service failed:", e)
