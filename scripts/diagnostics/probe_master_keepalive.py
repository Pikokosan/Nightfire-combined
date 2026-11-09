import argparse
import time

import serial

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from probe_usb_bridge import request


def status(port, sequence):
    data = bytes(request(port, sequence, 0x19).payload)
    if len(data) != 14 or data[0] != 1:
        raise RuntimeError('Unsupported RF heartbeat status')
    return {'state': data[1], 'interval_ms': int.from_bytes(data[2:4], 'big'),
            'age_ms': int.from_bytes(data[4:6], 'big'),
            'sent': int.from_bytes(data[6:10], 'big'),
            'failures': int.from_bytes(data[10:14], 'big')}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    options = parser.parse_args()
    with serial.Serial(options.port, 115200, timeout=0.2, write_timeout=2) as port:
        sequence = 0

        def call(command, payload=b''):
            nonlocal sequence
            sequence = (sequence + 1) & 255
            return request(port, sequence, command, payload)

        initial = status(port, 250)
        print('INITIAL', initial, flush=True)
        for _ in range(9):
            call(0x18, b'HBCK')
            time.sleep(0.15)
            sequence = (sequence + 1) & 255
            active = status(port, sequence)
            print('ACTIVE', active, flush=True)
            time.sleep(0.85)
        if active['state'] != 1 or active['sent'] - initial['sent'] < 3 or active['failures'] != initial['failures']:
            raise RuntimeError('RF heartbeat did not transmit successfully at the expected interval')
        # Do not renew the host heartbeat: RF keepalives must stop at host expiry.
        time.sleep(3.4)
        sequence = (sequence + 1) & 255
        stopped = status(port, sequence)
        time.sleep(3)
        sequence = (sequence + 1) & 255
        quiet = status(port, sequence)
        print('STOPPED', stopped, 'LATER', quiet, flush=True)
        if stopped['state'] != 0 or quiet['sent'] != stopped['sent']:
            raise RuntimeError('RF heartbeat continued after the host heartbeat expired')
        call(0x18, b'RENW')
        time.sleep(0.2)
        sequence = (sequence + 1) & 255
        renewed = status(port, sequence)
        print('RENEWED', renewed, flush=True)
        if renewed['state'] != 1 or renewed['sent'] <= quiet['sent']:
            raise RuntimeError('RF heartbeat did not restart after host renewal')
        print('RF keepalive interval, host-loss stop and recovery verified.', flush=True)


if __name__ == '__main__':
    main()
