import argparse
import time

import serial

from test_nrf_radio_pair import (
    CTRL_B,
    enter_raw_repl,
    exec_raw,
    soft_reset,
    stop_running_program,
)


WIFI_OVERLAP_MIN_CHANNEL = 0
WIFI_OVERLAP_MAX_CHANNEL = 73
DEFAULT_CHANNELS = list(range(74, 101))


def parse_channels(text):
    channels = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            channels.extend(range(int(start), int(end) + 1))
        else:
            channels.append(int(part))
    return [ch for ch in channels if 0 <= ch <= 100]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM13")
    parser.add_argument("--duration-ms", type=int, default=80)
    parser.add_argument("--interval-ms", type=int, default=2)
    parser.add_argument("--channels", default="")
    parser.add_argument(
        "--include-wifi-overlap",
        action="store_true",
        help="Allow nRF channels 0-73, which overlap or sit very near Wi-Fi channels 1-11.",
    )
    args = parser.parse_args()

    if args.channels:
        channels = parse_channels(args.channels)
    elif args.include_wifi_overlap:
        channels = list(range(0, 101))
    else:
        channels = DEFAULT_CHANNELS[:]

    if not args.include_wifi_overlap:
        channels = [
            ch for ch in channels
            if not (WIFI_OVERLAP_MIN_CHANNEL <= ch <= WIFI_OVERLAP_MAX_CHANNEL)
        ]

    if not channels:
        raise SystemExit("No channels to scan after applying Wi-Fi overlap filter.")

    port = serial.Serial(args.port, 115200, timeout=0.1, write_timeout=1)
    try:
        print("Stopping app on", args.port)
        print(repr(stop_running_program(port)[-200:]))
        enter_raw_repl(port)

        code = """
import nrf_radio

channels = {channels}
results = []
for ch in channels:
    sample = nrf_radio.noise_sample(ch, duration_ms={duration_ms}, interval_ms={interval_ms})
    results.append(sample)
    print("CH %03d avg=%4d min=%4d max=%4d count=%d" % (
        sample["channel"],
        sample["avg"],
        sample["min"],
        sample["max"],
        sample["count"],
    ))
results.sort(key=lambda item: item["avg"])
print("BEST")
for item in results[:8]:
    print("CH %03d avg=%4d min=%4d max=%4d count=%d" % (
        item["channel"],
        item["avg"],
        item["min"],
        item["max"],
        item["count"],
    ))
""".format(
            channels=channels,
            duration_ms=args.duration_ms,
            interval_ms=args.interval_ms,
        )

        out = exec_raw(port, code, timeout=max(15, len(channels) * args.duration_ms / 1000 + 10))
        print(out.decode("utf-8", "replace"))
        port.write(CTRL_B)
        time.sleep(0.2)
        print("Resetting app...")
        print(repr(soft_reset(port)[-300:]))
    finally:
        port.close()


if __name__ == "__main__":
    main()
