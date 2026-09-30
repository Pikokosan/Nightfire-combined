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
