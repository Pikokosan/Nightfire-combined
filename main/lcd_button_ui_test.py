import time

import machine
from machine import I2C, Pin

from lcd1602_i2c import I2cLcd1602

try:
    import nrf_node_config as node_config
except Exception:
    node_config = None


APP_NAME = "lcd-ui-test"
APP_VERSION = "0.1.0"


def cfg(name, default):
    return getattr(node_config, name, default)


LCD_I2C_ID = cfg("LCD_I2C_ID", 0)
LCD_SDA_PIN = cfg("LCD_SDA_PIN", 26)
LCD_SCL_PIN = cfg("LCD_SCL_PIN", 27)
LCD_I2C_FREQ = cfg("LCD_I2C_FREQ", 100000)
LCD_ADDRESS = cfg("LCD_ADDRESS", None)
LCD_MAPPING = cfg("LCD_MAPPING", "upper")
LCD_COLS = cfg("LCD_COLS", 16)
LCD_ROWS = cfg("LCD_ROWS", 2)
LCD_REFRESH_MS = cfg("LCD_REFRESH_MS", 250)

UP_BUTTON_PIN = cfg("UP_BUTTON_PIN", 45)
DOWN_BUTTON_PIN = cfg("DOWN_BUTTON_PIN", 47)
OK_FWD_BUTTON_PIN = cfg("OK_FWD_BUTTON_PIN", cfg("OK_BUTTON_PIN", 15))
BACK_BUTTON_PIN = cfg("BACK_BUTTON_PIN", 20)
BUTTON_DEBOUNCE_MS = cfg("BUTTON_DEBOUNCE_MS", 80)

GROUP_ID = cfg("GROUP_ID", 1)
NETWORK_ID = cfg("NETWORK_ID", 0x04D2)
RADIO_CHANNEL = cfg("RADIO_CHANNEL", 40)
CUE_COUNT = cfg("CUE_COUNT", 18)
BATTERY_PACK_TYPE = cfg("BATTERY_PACK_TYPE", "2S")
SIM_BATTERY_MV = cfg("SIM_BATTERY_MV", 8200)
CONTINUITY_PAGE_SIZE = cfg("CONTINUITY_PAGE_SIZE", 8)
MASTER_LINK_TIMEOUT_MS = cfg("MASTER_LINK_TIMEOUT_MS", 15000)


def clamp_cue_count(value):
    try:
        value = int(value)
    except Exception:
        value = 18
    if value < 1:
        return 1
    if value > 64:
        return 64
    return value


CUE_COUNT = clamp_cue_count(CUE_COUNT)


def uid32():
    try:
        uid = machine.unique_id()
        if uid:
            return int.from_bytes(uid[-4:], "big") & 0xFFFFFFFF
    except Exception:
        pass
    return 0x52840000


class DebouncedButton:
    def __init__(self, pin_id, name, debounce_ms=80):
        self.name = name
        self.pin_id = pin_id
        self.pin = None
        self.debounce_ms = int(debounce_ms)
        self.last_raw = False
        self.stable = False
        self.changed_ms = time.ticks_ms()
        self.ok = False
        self.error = ""
        self.init_pin()

    def init_pin(self):
        if self.pin_id is None:
            self.error = "not mapped"
            return
        try:
            self.pin = Pin(self.pin_id, Pin.IN, Pin.PULL_UP)
            self.ok = True
            self.last_raw = self.read_raw()
            self.stable = self.last_raw
        except Exception as e:
            self.error = str(e)
            print("%s button init failed:" % self.name, e)

    def read_raw(self):
        if self.pin is None:
            return False
        try:
            return self.pin.value() == 0
        except Exception:
            return False

    def update(self, now):
        raw = self.read_raw()
        if raw != self.last_raw:
            self.last_raw = raw
            self.changed_ms = now
            return False

        if raw != self.stable and time.ticks_diff(now, self.changed_ms) >= self.debounce_ms:
            self.stable = raw
            return self.stable

        return False

    def state_char(self):
        if not self.ok:
            return "X"
        return "1" if self.read_raw() else "0"


class LcdUiTest:
    def __init__(self):
        self.uid = uid32()
        self.lcd = None
        self.i2c_scan = []
        self.lcd_error = ""
        self.mode = "home"
        self.menu_index = 0
        self.view_name = ""
        self.detail_index = -1
        self.edit_name = ""
        self.edit_value = 0
        self.force_refresh = True
        self.last_refresh_ms = 0
        self.last_button = "--"
        self.scan_count = 0
        self.open_mask = self.default_open_mask()
        self.last_scan_ms = time.ticks_ms()
        self.last_master_rx_ms = time.ticks_ms()
        self.group_id = GROUP_ID & 0xFFFF
        self.network_id = NETWORK_ID & 0xFFFF
        self.radio_channel = RADIO_CHANNEL & 0xFF
        self.menu_items = (
            "System Info",
            "Continuity",
            "Buttons",
            "I2C",
            "Set Group",
            "Set Network",
            "Set Channel",
        )
        self.buttons = [
            DebouncedButton(UP_BUTTON_PIN, "UP", BUTTON_DEBOUNCE_MS),
            DebouncedButton(DOWN_BUTTON_PIN, "DOWN", BUTTON_DEBOUNCE_MS),
            DebouncedButton(OK_FWD_BUTTON_PIN, "OK", BUTTON_DEBOUNCE_MS),
            DebouncedButton(BACK_BUTTON_PIN, "BACK", BUTTON_DEBOUNCE_MS),
        ]

    def default_open_mask(self):
        mask = (1 << CUE_COUNT) - 1
        first_good = min(4, CUE_COUNT)
        for cue in range(1, first_good + 1):
            mask &= ~(1 << (cue - 1))
        return mask

    def init_lcd(self):
        try:
            try:
                i2c = I2C(
                    LCD_I2C_ID,
                    sda=Pin(LCD_SDA_PIN),
                    scl=Pin(LCD_SCL_PIN),
                    freq=LCD_I2C_FREQ,
                )
            except TypeError:
                i2c = I2C(LCD_I2C_ID, sda=Pin(LCD_SDA_PIN), scl=Pin(LCD_SCL_PIN))

            self.i2c_scan = i2c.scan()
            address = I2cLcd1602.find_address(i2c, LCD_ADDRESS)
            if address is None:
                self.lcd_error = "no lcd addr"
                print("LCD not found. I2C scan:", self.i2c_scan)
                return

            self.lcd = I2cLcd1602(i2c, address, LCD_COLS, LCD_ROWS, mapping=LCD_MAPPING)
            self.lcd.show_lines("LCD UI Test", "Starting...")
            print("LCD ready at 0x%02X mapping=%s" % (address, LCD_MAPPING))
        except Exception as e:
            self.lcd_error = str(e)
            print("LCD init failed:", e)

    def count_good_cues(self):
        good = 0
        for cue in range(1, CUE_COUNT + 1):
            if not self.open_mask & (1 << (cue - 1)):
                good += 1
        return good

    def detail_pages(self):
        page_size = max(1, int(CONTINUITY_PAGE_SIZE or 1))
        return max(1, (CUE_COUNT + page_size - 1) // page_size)

    def scan_continuity(self):
        self.scan_count += 1
        mode = self.scan_count % 4

        if mode == 0:
            self.open_mask = self.default_open_mask()
        elif mode == 1:
            mask = 0
            for cue in range(1, CUE_COUNT + 1):
                if cue % 2 == 0:
                    mask |= 1 << (cue - 1)
            self.open_mask = mask
        elif mode == 2:
            self.open_mask = 0
        else:
            self.open_mask = 0
            for cue in (2, 5, 9, 13):
                if cue <= CUE_COUNT:
                    self.open_mask |= 1 << (cue - 1)

        self.last_scan_ms = time.ticks_ms()
        self.force_refresh = True

    def open_menu_item(self):
        item = self.menu_items[self.menu_index]
        if item == "System Info":
            self.mode = "view"
            self.view_name = "system"
        elif item == "Continuity":
            self.mode = "view"
            self.view_name = "continuity"
            self.detail_index = -1
        elif item == "Buttons":
            self.mode = "view"
            self.view_name = "buttons"
        elif item == "I2C":
            self.mode = "view"
            self.view_name = "i2c"
        elif item == "Set Group":
            self.start_edit("group", self.group_id)
        elif item == "Set Network":
            self.start_edit("network", self.network_id)
        elif item == "Set Channel":
            self.start_edit("channel", self.radio_channel)

    def start_edit(self, name, value):
        self.mode = "edit"
        self.edit_name = name
        self.edit_value = int(value or 0)

    def adjust_edit(self, delta):
        if self.edit_name == "channel":
            self.edit_value = (self.edit_value + delta) % 101
        else:
            self.edit_value = (self.edit_value + delta) & 0xFFFF

    def save_edit(self):
        if self.edit_name == "group":
            self.group_id = self.edit_value & 0xFFFF
        elif self.edit_name == "network":
            self.network_id = self.edit_value & 0xFFFF
        elif self.edit_name == "channel":
            self.radio_channel = self.edit_value % 101
        self.mode = "menu"

    def handle_button(self, name):
        self.last_button = name

        if self.mode == "home":
            if name == "OK":
                self.mode = "menu"
            self.force_refresh = True
            return

        if self.mode == "menu":
            if name == "UP":
                self.menu_index = (self.menu_index - 1) % len(self.menu_items)
            elif name == "DOWN":
                self.menu_index = (self.menu_index + 1) % len(self.menu_items)
            elif name == "BACK":
                self.mode = "home"
            elif name == "OK":
                self.open_menu_item()
            self.force_refresh = True
            return

        if self.mode == "view":
            if name == "BACK":
                self.mode = "menu"
            elif self.view_name == "continuity" and name == "OK":
                self.show("Checking cues", "Simulated...")
                time.sleep_ms(250)
                self.scan_continuity()
            elif self.view_name == "continuity" and name == "UP":
                self.detail_index -= 1
                if self.detail_index < -1:
                    self.detail_index = self.detail_pages() - 1
            elif self.view_name == "continuity" and name == "DOWN":
                self.detail_index += 1
                if self.detail_index >= self.detail_pages():
                    self.detail_index = -1
            elif name == "OK":
                self.mode = "menu"
            self.force_refresh = True
            return

        if self.mode == "edit":
            if name == "UP":
                self.adjust_edit(1)
            elif name == "DOWN":
                self.adjust_edit(-1)
            elif name == "BACK":
                self.mode = "menu"
            elif name == "OK":
                self.save_edit()

        self.force_refresh = True

    def service_buttons(self):
        now = time.ticks_ms()
        for button in self.buttons:
            if button.update(now):
                self.handle_button(button.name)

    def battery_text(self):
        mv = int(SIM_BATTERY_MV)
        return "%d.%02dV %s" % (mv // 1000, (mv % 1000) // 10, BATTERY_PACK_TYPE)

    def battery_short_text(self):
        mv = int(SIM_BATTERY_MV)
        return "%d.%02dV" % (mv // 1000, (mv % 1000) // 10)

    def home_top_line(self):
        left = self.battery_short_text()
        right = "G%04X" % (self.group_id & 0xFFFF)
        gap = LCD_COLS - len(left) - len(right)
        if gap < 1:
            gap = 1
        return left + (" " * gap) + right

    def master_link_text(self):
        age = time.ticks_diff(time.ticks_ms(), self.last_master_rx_ms)
        return "M:OK" if age <= MASTER_LINK_TIMEOUT_MS else "M:--"

    def home_bottom_line(self):
        return "%s %08X" % (self.master_link_text(), self.uid)

    def cue_detail_lines(self, detail_index):
        page_size = max(1, int(CONTINUITY_PAGE_SIZE or 1))
        start = detail_index * page_size + 1
        end = min(CUE_COUNT, start + page_size - 1)
        parts = []
        for cue in range(start, end + 1):
            is_open = bool(self.open_mask & (1 << (cue - 1)))
            parts.append("%02d%s" % (cue, " " if is_open else chr(255)))

        return " ".join(parts[:4]), " ".join(parts[4:8])

    def menu_lines(self):
        item = self.menu_items[self.menu_index]
        next_item = self.menu_items[(self.menu_index + 1) % len(self.menu_items)]
        return ">%s" % item, " %s" % next_item

    def edit_lines(self):
        if self.edit_name == "channel":
            return "Channel", "%03d OK=save" % self.edit_value
        if self.edit_name == "network":
            return "Network ID", "%04X OK=save" % (self.edit_value & 0xFFFF)
        return "Group ID", "%04X OK=save" % (self.edit_value & 0xFFFF)

    def page_lines(self):
        if self.mode == "home":
            return self.home_top_line(), self.home_bottom_line()

        if self.mode == "menu":
            return self.menu_lines()

        if self.mode == "edit":
            return self.edit_lines()

        if self.view_name == "system":
            return "FW %s C%02d" % (APP_VERSION, CUE_COUNT), "Net%04X Ch%03d" % (self.network_id & 0xFFFF, self.radio_channel & 0xFF)

        if self.view_name == "continuity" and self.detail_index < 0:
            good = self.count_good_cues()
            return "Cont %02d/%02d SIM" % (good, CUE_COUNT), "OK=scan UP/DN"

        if self.view_name == "buttons":
            states = "U%s D%s O%s B%s" % (
                self.buttons[0].state_char(),
                self.buttons[1].state_char(),
                self.buttons[2].state_char(),
                self.buttons[3].state_char(),
            )
            return "Buttons %s" % self.last_button, states

        if self.view_name == "i2c":
            if self.i2c_scan:
                found = " ".join("0x%02X" % a for a in self.i2c_scan[:2])
            else:
                found = self.lcd_error or "none"
            return "I2C SDA%d SCL%d" % (LCD_SDA_PIN, LCD_SCL_PIN), found

        return self.cue_detail_lines(self.detail_index)

    def show(self, line1, line2):
        if self.lcd is None:
            print("%s | %s" % (line1, line2))
            return
        self.lcd.show_lines(line1, line2)

    def service_display(self):
        now = time.ticks_ms()
        if not self.force_refresh and time.ticks_diff(now, self.last_refresh_ms) < LCD_REFRESH_MS:
            return

        line1, line2 = self.page_lines()
        self.show(line1, line2)
        self.last_refresh_ms = now
        self.force_refresh = False

    def run(self):
        print("%s %s uid=0x%08X" % (APP_NAME, APP_VERSION, self.uid))
        print("LCD pins SDA=%s SCL=%s" % (LCD_SDA_PIN, LCD_SCL_PIN))
        print(
            "Buttons UP=%s DOWN=%s OK=%s BACK=%s" %
            (UP_BUTTON_PIN, DOWN_BUTTON_PIN, OK_FWD_BUTTON_PIN, BACK_BUTTON_PIN)
        )
        self.init_lcd()
        self.force_refresh = True

        while True:
            self.service_buttons()
            self.service_display()
            time.sleep_ms(10)


LcdUiTest().run()
