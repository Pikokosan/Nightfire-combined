"""Verify SET_GROUP/ACK/readback by reapplying the module's current group only."""
import argparse
import serial

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from probe_usb_bridge import request
from probe_usb_modules import receive
import nfcp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', required=True)
    parser.add_argument('--uid', required=True, type=lambda value: int(value, 0))
    options = parser.parse_args()
    protocol = nfcp.NFCP(1)
    sequence = 0
    with serial.Serial(options.port, 115200, timeout=0.1, write_timeout=2) as port:
        def call(command, data=b''):
            nonlocal sequence
            sequence = (sequence + 1) & 255
            return request(port, sequence, command, data)

        def discover(nonce):
            for _ in range(3):
                call(0x18, b'GCHK')
                call(0x10, protocol.build_discover_broadcast(nonce))
                for packet, _rssi in receive(port, 0.8):
                    data = packet['data']
                    if packet['src'] != options.uid or packet['cmd'] != nfcp.CMD_DISCOVER_RSP or len(data) not in (10, 12):
                        continue
                    if len(data) == 12 and int.from_bytes(data[10:12], 'big') != nonce:
                        continue
                    return int.from_bytes(data[:2], 'big')
            raise RuntimeError('Target module did not answer discovery')

        group = discover(0xA1B2)
        print('CURRENT uid=0x%08X group=%d' % (options.uid, group), flush=True)
        call(0x18, b'GCHK')
        call(0x10, protocol.build_set_group(options.uid, group))
        ack = None
        for packet, _rssi in receive(port, 4):
            print('REPLY uid=0x%08X command=0x%02X data=%s' %
                  (packet['src'], packet['cmd'], bytes(packet['data']).hex()), flush=True)
            if packet['src'] == options.uid and packet['cmd'] in (nfcp.CMD_ACK, nfcp.CMD_NACK) and packet['data'][:1] == b'\x31':
                ack = packet
                break
        if ack is None or ack['cmd'] != nfcp.CMD_ACK or ack['data'] != b'\x31\x00':
            raise RuntimeError('No successful SET_GROUP acknowledgement: %r' % ack)
        confirmed = discover(0xA1B3)
        if confirmed != group:
            raise RuntimeError('Group readback mismatch')
        print('SET_GROUP ACK and readback verified; group remains %d.' % group, flush=True)


if __name__ == '__main__':
    main()
