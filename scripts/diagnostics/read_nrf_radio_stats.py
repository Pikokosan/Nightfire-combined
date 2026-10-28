import argparse
import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import nrf_radio
print("RADIO_STATS", nrf_radio.stats())
try:
    import nrf_node_config as config
    print("NET", "0x%04X" % (getattr(config, "NETWORK_ID", 0x04D2) & 0xFFFF))
    print("CH", getattr(config, "RADIO_CHANNEL", 90))
except Exception as e:
    print("CONFIG_STATS_ERROR", e)
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=8.0)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=0.05, write_timeout=2) as port:
        port.dtr = False
        port.rts = False
        time.sleep(0.2)
        port.reset_input_buffer()
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=args.timeout).decode("utf-8", "replace")
            sys.stdout.write(output)
            sys.stdout.flush()
        finally:
            try:
                raw.exec_raw(port, "import machine\nmachine.reset()\n", timeout=3)
            except Exception:
                pass
            try:
                raw.exit_raw_repl(port)
            except Exception:
                pass


if __name__ == "__main__":
    main()
