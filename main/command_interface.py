import bridge_radio


class CommandInterface:
    def __init__(self, config, pin_factory=None, led_write=None, clock=None, difference=None,
                 output_factory=None):
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
        # Optional GPIO that switches the LED supply (on the nice!nano, P0.13
        # drives the VCC pin's switch: high = on). It is driven on only when
        # the LED output is valid; otherwise it is left untouched.
        self.power_pin = getattr(config, "STATUS_LED_POWER_PIN", None)
        self.power_on = 1 if getattr(config, "STATUS_LED_POWER_ON_VALUE", 1) else 0
        # Status LED brightness, 1-255 (255 = full). The default, 12, is the
        # dim level these pixels have always used.
        self.brightness = self.DEFAULT_BRIGHTNESS
        self.set_brightness(getattr(config, "STATUS_LED_BRIGHTNESS", self.DEFAULT_BRIGHTNESS))
        self.power = None
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
        # Safety latch: the ARM key must be seen in SAFE after power-on and
        # after each new app session before it can arm anything. Set here and
        # in heartbeat(); cleared by poll() once the switch settles on SAFE.
        self.need_safe = True
        self.led_write = led_write
        self.last_colors = None
        self.led_shown = None
        self.keepalive_supported = False
        self.group_settings_supported = False
        self.fire_pulse_supported = False
        self.pairing_supported = False
        # Fed by the main loop: MasterControl.arm_state() (None without master
        # firmware) and the tick of the last RF keepalive sent.
        self.module_arm = None
        self.keepalive_at = None
        for name, fault in (("switch_pin", 1), ("led_pin", 2), ("power_pin", 2)):
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
                valid = (self.led_pin != self.switch_pin and
                         (self.power_pin is None or
                          self.power_pin not in (self.led_pin, self.switch_pin)) and
                         sorted(self.order) == ["B", "G", "R"] and
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
        if self.led_status == 1 and self.power_pin is not None:
            try:
                if output_factory is None:
                    from machine import Pin
                    output_factory = lambda number, value: Pin(number, Pin.OUT, value=value)
                self.power = output_factory(self.power_pin, self.power_on)
            except Exception:
                self.led_status = 2
                self.faults |= 2

    def set_brightness(self, value):
        """Set the status LED brightness (1-255); returns False if invalid."""
        if type(value) is not int or not 1 <= value <= 255:
            return False
        self.brightness = value
        self.last_colors = None   # rewrite the pixels on the next poll
        return True

    def scale(self, color):
        level = self.brightness
        return tuple(0 if c == 0 else max(1, (c * level + 127) // 255) for c in color)

    def heartbeat(self, payload):
        if len(payload) != 4:
            return False
        if self.session_state() != 1 or bytes(payload) != self.session_id:
            # A new app session (the app picks a new tag on every connect, so
            # a quick reconnect counts too): the key has to go through SAFE
            # again.
            self.need_safe = True
        self.session_id = bytes(payload)
        self.last_host = self.clock()
        return True

    def key_latched(self):
        """True while the ARM key was on at power-on or app connect and has
        not been turned to SAFE since."""
        return self.input is not None and self.need_safe and self.switch == 2

    def arm_switch(self):
        """The switch position that may permit ARM/FIRE: None without a
        switch, and SAFE while the key is latched."""
        if self.input is None:
            return None
        return 1 if self.need_safe else self.switch

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
                    if value == 1:
                        self.need_safe = False
                self.faults &= ~1
            except Exception:
                self.switch = 0
                self.faults |= 1
        session = self.session_state()
        self.faults = ((self.faults & ~44) | (4 if session == 2 else 0) | (0 if radio_ready else 8) |
                       (32 if self.key_latched() else 0))
        if self.led_status != 1:
            return
        # Everything the pixels show (see colors()). Working the colours out
        # takes about 3 ms on the nice!nano, and the bridge hears no radio
        # meanwhile, so it is only done when this changes.
        shown = (self.switch, self.need_safe, self.module_arm, session, self.faults,
                 (now // 500) % 2,
                 self.keepalive_at is not None and
                 self.difference(now, self.keepalive_at) < self.KEEPALIVE_BLINK_MS)
        if shown == self.led_shown and self.last_colors is not None:
            return
        self.led_shown = shown
        colors = [self.scale(color) for color in self.colors(now, session)]
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

    # Full-brightness colours. At the default brightness (12) they scale to
    # GREEN (0, 10, 0), RED (12, 0, 0), AMBER (10, 5, 0), BLUE (0, 0, 12)
    # and YELLOW (10, 9, 0).
    GREEN = (0, 212, 0)
    RED = (255, 0, 0)
    AMBER = (212, 106, 0)
    BLUE = (0, 0, 255)
    YELLOW = (212, 191, 0)
    DEFAULT_BRIGHTNESS = 12
    OFF = (0, 0, 0)
    KEY_LATCHED = 32   # fault bit: ARM key on at power-on/connect
    KEEPALIVE_BLINK_MS = 150

    def colors(self, now, session):
        """Pixel 1 arm switch, 2 field arm state, 3 heartbeats, 4 fault."""
        flash = (now // 500) % 2 == 0
        if self.input is None:
            switch = self.OFF
        elif self.key_latched():
            switch = self.YELLOW if flash else self.OFF
        elif self.switch == 1:
            switch = self.GREEN
        elif self.switch == 2:
            switch = self.RED
        else:
            switch = self.AMBER if flash else self.OFF
        if self.module_arm == 0:
            modules = self.GREEN
        elif self.module_arm == 1:
            modules = self.RED
        else:
            modules = self.AMBER
        if session != 1:
            heartbeat = self.OFF
        elif (self.keepalive_at is not None and
              self.difference(now, self.keepalive_at) < self.KEEPALIVE_BLINK_MS):
            heartbeat = self.GREEN
        else:
            heartbeat = self.BLUE
        # The key latch has its own yellow pixel 1; it is not a fault light.
        fault = self.RED if self.faults & ~self.KEY_LATCHED and flash else self.OFF
        return [switch, modules, heartbeat, fault]

    def payload(self):
        age = 65535 if self.last_host is None else min(65535, max(0, self.difference(self.clock(), self.last_host)))
        capabilities = (128 if self.fire_pulse_supported else 0) | (4 if self.pairing_supported else 0) | 64 | 8 | (32 if self.group_settings_supported else 0) | (16 if self.keepalive_supported else 0) | (1 if self.input is not None else 0) | (2 if self.led_status == 1 else 0)
        return (bytes([1, capabilities, self.switch, self.led_status, self.session_state(), self.faults]) +
                bridge_radio.int_to_be(age, 2) + bridge_radio.int_to_be(self.timeout, 2) +
                bytes([255 if self.switch_pin is None else self.switch_pin,
                       255 if self.led_pin is None else self.led_pin, self.led_count]) +
                bridge_radio.int_to_be(self.rejected, 2))
