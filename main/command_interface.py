import bridge_radio


class CommandInterface:
    def __init__(self, config, pin_factory=None, led_write=None, clock=None, difference=None):
        if clock is None:
            import time
            clock = time.ticks_ms
            difference = time.ticks_diff
        self.clock = clock
        self.difference = difference
        self.switch_pin = getattr(config, "ARM_SWITCH_PIN", None)
        self.led_pin = getattr(config, "STATUS_LED_PIN", None)
        try:
            self.led_count = int(getattr(config, "STATUS_LED_COUNT", 4))
        except Exception:
            self.led_count = 0
        self.order = getattr(config, "STATUS_LED_ORDER", "RGB")
        self.timing = getattr(config, "STATUS_LED_TIMING_NS", None)
        self.timeout = 3000
        self.switch = 0
        self.led_status = 0
        self.faults = 0
        self.rejected = 0
        self.last_host = None
        self.last_rx = None
        self.session_id = None
        self.input = None
        self.candidate = None
        self.changed_at = self.clock()
        self.led_write = led_write
        self.last_colors = None
        self.keepalive_supported = False
        self.group_settings_supported = False
        for name, fault in (("switch_pin", 1), ("led_pin", 2)):
            number = getattr(self, name)
            if number is not None and (type(number) is not int or number < 0 or number > 47):
                setattr(self, name, None)
                self.faults |= fault
        if self.led_count < 4 or self.led_count > 16:
            self.led_count = 4
            self.faults |= 2
        if self.switch_pin is not None:
            try:
                if pin_factory is None:
                    from machine import Pin
                    pin_factory = lambda number: Pin(number, Pin.IN, Pin.PULL_UP)
                self.input = pin_factory(self.switch_pin)
            except Exception:
                self.faults |= 1
        if self.led_pin is not None:
            try:
                valid = (self.led_pin != self.switch_pin and sorted(self.order) == ["B", "G", "R"] and
                         self.timing is not None and len(self.timing) == 4 and
                         all(type(value) is int and 100 <= value <= 5000 for value in self.timing))
            except Exception:
                valid = False
            if not valid:
                self.led_status = 2
                self.faults |= 2
            elif self.led_write is None:
                self.led_status = 3
                self.faults |= 2
            else:
                self.led_status = 1

    def heartbeat(self, payload):
        if len(payload) != 4:
            return False
        self.session_id = bytes(payload)
        self.last_host = self.clock()
        return True

    def session_state(self):
        if self.last_host is None:
            return 0
        return 1 if self.difference(self.clock(), self.last_host) <= self.timeout else 2

    def record_rx(self):
        self.last_rx = self.clock()

    def reject(self):
        self.rejected = min(65535, self.rejected + 1)

    def poll(self, radio_ready):
        now = self.clock()
        if self.input is not None:
            try:
                raw = self.input.value()
                if raw not in (0, 1):
                    raise ValueError("invalid switch reading")
                value = 2 if raw == 0 else 1
                if value != self.candidate:
                    self.candidate = value
                    self.changed_at = now
                    self.switch = 0
                elif self.difference(now, self.changed_at) >= 50:
                    self.switch = value
                self.faults &= ~1
            except Exception:
                self.switch = 0
                self.faults |= 1
        session = self.session_state()
        self.faults = (self.faults & ~12) | (4 if session == 2 else 0) | (0 if radio_ready else 8)
        if self.led_status != 1:
            return
        recent_rx = self.last_rx is not None and self.difference(now, self.last_rx) <= self.timeout
        colors = [(0, 0, 12) if session == 1 else (0, 0, 0),
                  (0, 10, 0) if radio_ready and recent_rx else (10, 5, 0),
                  (10, 5, 0),
                  (12, 0, 0) if self.faults and (now // 500) % 2 == 0 else (0, 0, 0)]
        colors += [(0, 0, 0)] * (self.led_count - 4)
        encoded = bytes(color["RGB".index(component)] for color in colors for component in self.order)
        if encoded == self.last_colors:
            return
        try:
            self.led_write(self.led_pin, encoded, self.timing)
            self.last_colors = encoded
        except Exception:
            self.led_status = 2
            self.faults |= 2

    def payload(self):
        age = 65535 if self.last_host is None else min(65535, max(0, self.difference(self.clock(), self.last_host)))
        capabilities = 8 | (32 if self.group_settings_supported else 0) | (16 if self.keepalive_supported else 0) | (1 if self.input is not None else 0) | (2 if self.led_status == 1 else 0)
        return (bytes([1, capabilities, self.switch, self.led_status, self.session_state(), self.faults]) +
                bridge_radio.int_to_be(age, 2) + bridge_radio.int_to_be(self.timeout, 2) +
                bytes([255 if self.switch_pin is None else self.switch_pin,
                       255 if self.led_pin is None else self.led_pin, self.led_count]) +
                bridge_radio.int_to_be(self.rejected, 2))
