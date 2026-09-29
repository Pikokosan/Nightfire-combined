import sys
import time

import serial

sys.path.insert(0, "scripts")
import raw_micropython_upload as raw


CODE = r'''
import nrf_dummy_main as d
import nfcp

def fake_tx(frame, repeats=3, gap_ms=20):
    print("TX", len(frame), bytes(frame[:12]), repeats, gap_ms)

d.tx_nfcp = fake_tx
tx = nfcp.NFCP(1)
frame = tx.build_discover_broadcast(0x2222)
payload = (d.NETWORK_ID & 0xFFFF).to_bytes(2, "big") + bytes([0x42]) + frame
d.handle_radio_payload(payload)
print("DONE")
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
