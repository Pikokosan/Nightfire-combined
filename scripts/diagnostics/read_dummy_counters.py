import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import sys
d = sys.modules.get("nrf_dummy_main")
print("MOD", d is not None)
if d:
    print("COUNTS", d.debug_radio_payloads, d.debug_nfcp_packets, d.debug_discover_packets)
'''


def main():
    port_name = sys.argv[1] if len(sys.argv) > 1 else "COM16"
    with serial.Serial(port_name, 115200, timeout=0.05, write_timeout=2) as port:
        port.dtr = False
        port.rts = False
        time.sleep(0.2)
        port.reset_input_buffer()
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        try:
            output = raw.exec_raw(port, CODE, timeout=8).decode("utf-8", "replace")
            sys.stdout.write(output)
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
