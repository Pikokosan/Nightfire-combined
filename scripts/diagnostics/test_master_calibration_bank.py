"""Exercise one complete resistance-calibration bank through the master."""

import __main__


TARGET_UID = 0xFE81F019
EXPECTED = (20, 47, 27, 9, 24, 102, 0, None)
CUE_COUNT = 24
BANK_INDEX = 0


raw = __main__.request_resistance_for_uid(TARGET_UID, CUE_COUNT, 4000, True)
print("CAL_RAW", raw)
print("CAL_RAW_PACKETS", __main__.get_last_packets())

points = []
if raw and raw.get("unit") == "adc_mv":
    values = raw.get("values", [])
    for position, expected in enumerate(EXPECTED):
        measured = values[position] if position < len(values) else None
        if expected is not None and measured is not None:
            points.append((int(measured), int(expected)))
    points.sort(key=lambda point: point[0])

print("CAL_POINTS", points)
saved = bool(points) and __main__.request_set_resistance_calibration(
    TARGET_UID,
    BANK_INDEX,
    points,
    3000,
)
print("CAL_SAVED", saved)

verified = __main__.request_resistance_for_uid(TARGET_UID, CUE_COUNT, 4000)
print("CAL_VERIFY", verified)
