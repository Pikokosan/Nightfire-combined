from __future__ import print_function

import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts/lib")
import raw_micropython_upload as raw


CODE = r'''
import nrf_radio

try:
    print("FICR32 0x%08X" % (nrf_radio.device_id32() & 0xFFFFFFFF))
except Exception as e:
    print("FICR32_ERROR", e)

try:
    print("FICR", nrf_radio.device_id())
except Exception as e:
    print("FICR_ERROR", e)

try:
    import nrf_node_config
    print("CONFIG_UID 0x%08X" % (nrf_node_config.UNIQUE_ID & 0xFFFFFFFF))
except Exception as e:
    print("CONFIG_ERROR", e)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM16")
    args = parser.parse_args()

    with serial.Serial(args.port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=5).decode("utf-8", "replace")
            sys.stdout.write(output)
            sys.stdout.flush()
        finally:
            try:
                raw.exit_raw_repl(port)
            except Exception:
                pass
            try:
                port.write(raw.CTRL_D)
                time.sleep(0.5)
            except Exception:
                pass


if __name__ == "__main__":
    main()
