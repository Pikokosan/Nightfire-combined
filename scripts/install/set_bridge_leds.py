"""Test and change the status LED settings on a USB bridge (dongle).

The bridge keeps its LED settings in its own nrf_bridge_config.py. Reinstalling
the bridge firmware keeps that file, so editing main/configs/bridge_usb.py does
not change a bridge that is already set up: use this script instead.

Show the current settings:
    py -3 scripts/install/set_bridge_leds.py --port COM12

Light a test pattern (pixel 1 red, 2 green, 3 blue, 4 white) without saving:
    py -3 scripts/install/set_bridge_leds.py --port COM12 --preset ws2811 --test

Save settings once the pattern looks right:
    py -3 scripts/install/set_bridge_leds.py --port COM12 --preset ws2811 --save

Any preset value can be overridden, for example
    --order RGB --timing 150,1100,550,700 --count 4

See docs/bridge_status_leds.md for how to read the test pattern.
"""
import argparse
import ast
import datetime
import sys
from pathlib import Path

import serial

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "lib"))
import raw_micropython_upload as raw  # noqa: E402

CONFIG = "nrf_bridge_config.py"

# Timings are what the bridge is asked to send, (T0H, T0L, T1H, T1L) in ns.
# The driver's real high pulses come out somewhat longer than asked for (on
# the bench WS2812Bs a requested 400 ns "0" already read as a "1"), so these
# sit below the datasheet values.
PRESETS = {
    "ws2812b": {"order": "GRB", "timing": (250, 1000, 800, 450),
                "note": "tested on the COM12 bench bridge"},
    "ws2811": {"order": "RGB", "timing": (150, 1100, 550, 700),
               "note": "800 kHz WS2811; tested on the COM12 bench bridge"},
    "ws2811-400k": {"order": "RGB", "timing": (350, 2000, 1100, 1300),
                    "note": "WS2811 wired for 400 kHz (slow) mode"},
    "sk6812": {"order": "GRB", "timing": (200, 1000, 600, 650),
               "note": "SK6812 RGB; starting point, check with --test"},
}

NAMES = ("STATUS_LED_PIN", "STATUS_LED_COUNT", "STATUS_LED_ORDER", "STATUS_LED_TIMING_NS",
         "STATUS_LED_POWER_PIN", "STATUS_LED_POWER_ON_VALUE", "STATUS_LED_BRIGHTNESS")
TEST_COLOURS = [(40, 0, 0), (0, 40, 0), (0, 0, 40), (25, 25, 25)]


def read_settings(text):
    """NAME = literal lines of a config file, comments ignored."""
    settings = {}
    for line in text.splitlines():
        name, equals, value = line.partition("=")
        name = name.strip()
        if not equals or not name.isidentifier():
            continue
        try:
            settings[name] = ast.literal_eval(value.split("#")[0].strip())
        except (ValueError, SyntaxError):
            pass
    return settings


def write_settings(text, changes):
    """Replace or append NAME = value lines, keeping everything else."""
    lines = text.splitlines()
    done = set()
    for index, line in enumerate(lines):
        name = line.partition("=")[0].strip()
        if name in changes:
            comment = line.partition("#")[2]
            lines[index] = "%s = %r" % (name, changes[name]) + ("  # " + comment.strip() if comment else "")
            done.add(name)
    lines += ["%s = %r" % (name, value) for name, value in changes.items() if name not in done]
    return "\n".join(lines).rstrip() + "\n"


def encode(colours, order, count):
    colours = list(colours) + [(0, 0, 0)] * (count - len(colours))
    return bytes(colour["RGB".index(channel)] for colour in colours for channel in order)


def device_read(port, name):
    output = raw.exec_raw(port, "import binascii\ntry:\n print(binascii.hexlify(open(%r,'rb').read()).decode())\n"
                                "except OSError:\n print('MISSING')\n" % name).strip()
    return None if output == b"MISSING" else bytes.fromhex(output.decode("ascii"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="the bridge's serial port, e.g. COM12")
    parser.add_argument("--preset", choices=sorted(PRESETS))
    parser.add_argument("--order", help="colour order, a mix of R, G and B, e.g. GRB")
    parser.add_argument("--timing", help="T0H,T0L,T1H,T1L in nanoseconds (100..5000 each)")
    parser.add_argument("--count", type=int, help="pixels on the data line, 4..16")
    parser.add_argument("--pin", type=int, help="LED data GPIO (P0.08 is 8)")
    parser.add_argument("--power-pin", help="GPIO that switches LED power, or 'none' (P0.13 is 13)")
    parser.add_argument("--brightness", type=int,
                        help="status LED brightness, 1..255 (default 12; the app can also change it)")
    parser.add_argument("--test", action="store_true", help="light a test pattern; nothing is saved")
    parser.add_argument("--leave", action="store_true",
                        help="with --test, leave the pattern lit and the bridge stopped instead of "
                             "waiting for Enter (run again, or unplug the bridge, to restart it)")
    parser.add_argument("--save", action="store_true", help="store the settings on the bridge and restart it")
    parser.add_argument("--repo", action="store_true",
                        help="with --save, also write them to main/configs/bridge_usb.py for new bridges")
    options = parser.parse_args()

    with serial.Serial(options.port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        restart = True
        try:
            original = device_read(port, CONFIG)
            if original is None:
                raise SystemExit("%s has no %s; install the bridge firmware first." % (options.port, CONFIG))
            text = original.decode("utf-8")
            current = read_settings(text)
            print("Bridge settings now:")
            for name in NAMES:
                print("  %-26s %r" % (name, current.get(name)))

            changes = {}
            if options.preset:
                preset = PRESETS[options.preset]
                print("Preset %s: %s" % (options.preset, preset["note"]))
                changes["STATUS_LED_ORDER"] = preset["order"]
                changes["STATUS_LED_TIMING_NS"] = preset["timing"]
            if options.order:
                changes["STATUS_LED_ORDER"] = options.order.upper()
            if options.timing:
                changes["STATUS_LED_TIMING_NS"] = tuple(int(value) for value in options.timing.split(","))
            if options.count is not None:
                changes["STATUS_LED_COUNT"] = options.count
            if options.pin is not None:
                changes["STATUS_LED_PIN"] = options.pin
            if options.power_pin is not None:
                changes["STATUS_LED_POWER_PIN"] = (None if options.power_pin.lower() == "none"
                                                   else int(options.power_pin))
            if options.brightness is not None:
                if not 1 <= options.brightness <= 255:
                    raise SystemExit("Brightness must be 1 to 255.")
                changes["STATUS_LED_BRIGHTNESS"] = options.brightness
            wanted = dict(current, **changes)

            order = wanted.get("STATUS_LED_ORDER", "RGB")
            timing = tuple(wanted.get("STATUS_LED_TIMING_NS") or ())
            count = wanted.get("STATUS_LED_COUNT", 4)
            pin = wanted.get("STATUS_LED_PIN")
            if sorted(order) != ["B", "G", "R"]:
                raise SystemExit("Colour order must use R, G and B once each, e.g. GRB.")
            if len(timing) != 4 or not all(100 <= value <= 5000 for value in timing):
                raise SystemExit("Timing must be four values from 100 to 5000 ns.")
            if not 4 <= count <= 16:
                raise SystemExit("Pixel count must be 4 to 16.")
            if pin is None:
                raise SystemExit("STATUS_LED_PIN is not set; pass --pin (the bench bridges use 8).")

            if options.test:
                power = wanted.get("STATUS_LED_POWER_PIN")
                code = "import nrf_radio\n"
                if power is not None:
                    code += "from machine import Pin\nPin(%d, Pin.OUT, value=%d)\n" % (
                        power, 1 if wanted.get("STATUS_LED_POWER_ON_VALUE", 1) else 0)
                    code += "import time\ntime.sleep_ms(20)\n"
                code += "nrf_radio.write_ws2811(%d, %r, %r)\n" % (pin, encode(TEST_COLOURS, order, count), timing)
                raw.exec_raw(port, code)
                print("\nTest pattern sent with order %s, timing %s:" % (order, timing))
                print("  pixel 1 red, pixel 2 green, pixel 3 blue, pixel 4 white.")
                print("All white means the 0 pulse (first timing value) is too long; try a smaller one.")
                print("Dark or missing colours mean the 1 pulse (third value) is too short; try a larger one.")
                print("Right brightness but wrong colours means the colour order is wrong.")
                if options.leave and not options.save:
                    restart = False
                    print("The bridge stays stopped with the pattern lit; run again or replug it to restart.")
                else:
                    input("Press Enter to restart the bridge... ")

            if options.save:
                if not changes:
                    raise SystemExit("Nothing to save; pass --preset, --order, --timing, --count, --pin, --power-pin or --brightness.")
                backup = ROOT / "dist" / ("bridge_backup_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
                backup.mkdir(parents=True)
                (backup / CONFIG).write_bytes(original)
                updated = write_settings(text, changes).encode("utf-8")
                local = backup / ("new_" + CONFIG)
                local.write_bytes(updated)
                raw.upload_file(port, str(local), CONFIG)
                if device_read(port, CONFIG) != updated:
                    raw.upload_file(port, str(backup / CONFIG), CONFIG)
                    raise RuntimeError("Verification failed; the previous settings were put back.")
                print("Saved on the bridge (previous file in %s)." % backup)
                if options.repo:
                    repo = ROOT / "main" / "configs" / "bridge_usb.py"
                    repo.write_text(write_settings(repo.read_text(encoding="utf-8"), changes), encoding="utf-8")
                    print("Also written to", repo.relative_to(ROOT))
            elif not options.test:
                restart = False
        finally:
            raw.exit_raw_repl(port)
            if restart:
                port.write(raw.CTRL_D)
    if restart:
        print("Bridge restarted.")


if __name__ == "__main__":
    main()
