import machine
import time

import nrf_radio


def valid_radio_channel(channel):
    return channel == 0 or (74 <= channel <= 100)


def get_default_unique_id(fallback):
    try:
        uid = nrf_radio.device_id32()
        if uid:
            return int(uid) & 0xFFFFFFFF
    except Exception:
        pass

    try:
        uid = machine.unique_id()
        if uid:
            return int.from_bytes(uid[-4:], "big") & 0xFFFFFFFF
    except Exception:
        pass

    return fallback & 0xFFFFFFFF


def send_with_cca(
    payload,
    enabled=True,
    cca_ms=2,
    busy_dbm=-85,
    retries=4,
    backoff_min_ms=2,
    backoff_max_ms=10,
    rand_fn=None,
):
    if not enabled:
        return bool(nrf_radio.send(payload))

    attempts = int(retries or 0) + 1
    if attempts < 1:
        attempts = 1

    min_backoff = int(backoff_min_ms or 0)
    max_backoff = int(backoff_max_ms or min_backoff)
    if max_backoff < min_backoff:
        max_backoff = min_backoff

    for attempt in range(attempts):
        if nrf_radio.send(
            payload,
            cca_ms=cca_ms,
            busy_dbm=busy_dbm,
            backoff_ms=max_backoff,
        ):
            return True

        if attempt + 1 < attempts and max_backoff > 0:
            span = max_backoff - min_backoff + 1
            extra = rand_fn(span) if rand_fn else 0
            time.sleep_ms(min_backoff + extra)

    return False


# --- 32 kHz clock -------------------------------------------------------------
# ticks_ms() and ticks_us() count RTC1, which runs on the 32 kHz clock. A board
# whose 32 kHz crystal doesn't start (seen on a Pro Micro nRF52840 clone)
# falls back to the internal RC oscillator, which can be ~1% off: a timed show
# would drift ~0.5 s a minute. The nRF52840 can calibrate the RC oscillator
# against the 32 MHz crystal (to about 500 ppm); it drifts with temperature, so
# that is repeated every few seconds. It's a hardware task: no CPU time.
LFRC_CLOCK = 0x40000000
LFRC_TASKS_CAL = LFRC_CLOCK + 0x010
LFRC_EVENTS_DONE = LFRC_CLOCK + 0x10C
LFRC_HFCLKSTAT = LFRC_CLOCK + 0x40C
LFRC_LFCLKSTAT = LFRC_CLOCK + 0x418
LFRC_CAL_EVERY_MS = 4000


class LfrcCalibrator:
    """Calibrates the 32 kHz RC oscillator when the board runs on it.
    Call service() from the main loop; it does nothing on a board whose
    32 kHz crystal runs."""

    def __init__(self, mem=None):
        self.active = False
        self.count = 0
        self.last_ms = 0
        try:
            self.mem = machine.mem32 if mem is None else mem
            stat = self.mem[LFRC_LFCLKSTAT]
        except Exception:
            return              # no CLOCK registers (PC tools, simulator)
        # Running (bit 16) from the RC oscillator (source 0).
        if (stat & 0x10000) and (stat & 3) == 0:
            self.active = True
            print("LFCLK on RC (32 kHz crystal not running): calibrating every %d s" %
                  (LFRC_CAL_EVERY_MS // 1000))
            self.calibrate(time.ticks_ms())

    def calibrate(self, now_ms):
        self.last_ms = now_ms
        # Only against the 32 MHz crystal (HFCLKSTAT source 1, running).
        if (self.mem[LFRC_HFCLKSTAT] & 0x10001) != 0x10001:
            return
        self.mem[LFRC_EVENTS_DONE] = 0
        self.mem[LFRC_TASKS_CAL] = 1
        self.count += 1

    def service(self):
        if self.active:
            now = time.ticks_ms()
            if time.ticks_diff(now, self.last_ms) >= LFRC_CAL_EVERY_MS:
                self.calibrate(now)
