"""Non-actuating checks for master firmware: status, heartbeat and discovery only."""
import argparse
import time

import serial
from probe_usb_bridge import request
from probe_usb_modules import receive
import bridge_radio
import nfcp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    options = parser.parse_args()
    sequence = 0
    with serial.Serial(options.port, 115200, timeout=0.1, write_timeout=2) as port:
        def query(command, payload=b''):
            nonlocal sequence
            sequence = sequence % 254 + 1
            return bytes(request(port, sequence, command, payload).payload)

        query(bridge_radio.CMD_PING)
        info = bridge_radio.parse_get_info(query(bridge_radio.CMD_GET_INFO))
        print('BRIDGE', info, flush=True)
        if info['firmware_version'] != 'usb-master-0.4.0':
            raise RuntimeError('Master firmware identity was not confirmed')
        initial_settings = info['network_id']
        stats = bridge_radio.parse_stats_payload(query(bridge_radio.CMD_GET_STATS))
        print('RADIO', stats, flush=True)
        query(0x18, b'MSTR')
        active = query(0x17)
        if len(active) != 15 or active[4] != 1:
            raise RuntimeError('Host heartbeat did not activate')
        print('HOST active, switch=%d, LED=%d, faults=%d' %
              (active[2], active[3], active[5]), flush=True)
        protocol = nfcp.NFCP(1)
        nonce = 0xC0DE
        modules = {}
        for unused in range(4):
            query(0x18, b'MSTR')
            query(bridge_radio.CMD_SEND_NFCP, protocol.build_discover_broadcast(nonce))
            for packet, rssi in receive(port, 0.6):
                data = packet['data']
                if packet['cmd'] == nfcp.CMD_DISCOVER_RSP and len(data) == 12 and (
                        int.from_bytes(data[10:12], 'big') == nonce and
                        int.from_bytes(data[2:6], 'big') == packet['src']):
                    modules[packet['src']] = data
                    print('DISCOVER 0x%08X group=%d cues=%d rssi=%d' %
                          (packet['src'], int.from_bytes(data[:2], 'big'), data[6], rssi), flush=True)
        for uid in modules:
            found = False
            for unused in range(4):
                query(0x18, b'MSTR')
                query(bridge_radio.CMD_SEND_NFCP, protocol.build_info_request(uid))
                for packet, rssi in receive(port, 1.0):
                    if packet['src'] == uid and packet['cmd'] == nfcp.CMD_INFO_RSP:
                        data = packet['data']
                        fields = []
                        offset = 0
                        while offset < len(data):
                            length = data[offset]
                            offset += 1
                            if offset + length > len(data):
                                raise RuntimeError('Malformed INFO response')
                            fields.append(bytes(data[offset:offset + length]).decode('utf8', 'replace'))
                            offset += length
                        print('MODULE INFO 0x%08X %r' % (uid, fields), flush=True)
                        found = True
                if found:
                    break
            if not found:
                raise RuntimeError('Module INFO timeout for 0x%08X' % uid)
            status_found = False
            for unused in range(4):
                query(0x18, b'MSTR')
                query(bridge_radio.CMD_SEND_NFCP, protocol.build_output_status_request(uid))
                for packet, rssi in receive(port, 1.2):
                    if packet['src'] != uid or packet['cmd'] != nfcp.CMD_OUTPUT_STATUS_RSP:
                        continue
                    status = bytes(packet['data'])
                    if len(status) != 27 or status[0] != 1:
                        raise RuntimeError('Malformed output status from 0x%08X' % uid)
                    if status[1] & 0x09 or any(status[3:11]):
                        raise RuntimeError('Module 0x%08X output hardware is not safely disarmed' % uid)
                    print('OUTPUT STATUS 0x%08X armed=0 driver=0 active=0 cues=%d seq=%d rssi=%d' %
                          (uid, status[2], int.from_bytes(status[19:23], 'big'), rssi), flush=True)
                    status_found = True
                if status_found:
                    break
            if not status_found:
                raise RuntimeError('Module output-status timeout for 0x%08X' % uid)
        query(0x18, b'MSTR')
        print('KEEPALIVE', query(0x19).hex(), flush=True)
        time.sleep(3.4)
        expired = query(0x17)
        if expired[4] != 2 or not expired[5] & 4:
            raise RuntimeError('Host session did not expire')
        print('HOST expiry verified', flush=True)
        query(0x18, b'MSTR')
        renewed = query(0x17)
        if renewed[4] != 1 or renewed[5] & 4:
            raise RuntimeError('Host session did not recover')
        final = bridge_radio.parse_get_info(query(3))
        if final['network_id'] != initial_settings:
            raise RuntimeError('Network setting changed unexpectedly')
        print('HOST recovery verified; network unchanged; %d module(s)' % len(modules), flush=True)
        print('No ARM, FIRE, script execution, measurements or settings writes were sent.', flush=True)
        if not modules:
            raise RuntimeError('No field discovery replies received')


if __name__ == '__main__':
    main()
