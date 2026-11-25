"""Generate Dart parity fixtures from the original MicroPython NFCP builders.

Writes into the nightfire-app repo: --app PATH, or NIGHTFIRE_APP, or the
sibling folder ../../nightfire-app.
"""
import argparse
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / 'main'))
import nfcp


def app_root():
    return Path(os.environ.get('NIGHTFIRE_APP') or root.parents[1] / 'nightfire-app')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--app', type=Path, default=app_root(), help='nightfire-app folder')
    args = ap.parse_args()
    if not (args.app / 'pubspec.yaml').exists():
        sys.exit('nightfire-app not found at %s (use --app)' % args.app)
    p = nfcp.NFCP(1)
    uid = 0x12ff5678
    frames = {
        'arm': p.build_arm_unique(uid),
        'disarm': p.build_disarm_unique(uid),
        'fire64': p.build_unique_fire(uid, 1 << 63),
        'fireAll': p.build_unique_fire(uid, (1 << 64) - 1),
        'setUnique': p.build_set_unique(uid, 0xff00ff00),
        'setNetwork': p.build_set_network(uid, 1234),
        'setChannel': p.build_set_radio_channel(uid, 90),
        'raw': p.build_resistance_request(uid, True),
        'outputStatus': p.build_output_status_request(uid),
        'calibrate': p.build_set_resistance_calibration(uid, 1, [(1234, 47), (65535, 0)]),
        'clear': p.build_script_clear(uid, 0xabcd),
        'load': p.build_script_load(uid, 0xabcd, 2, [(1000, 1), (2000, (1 << 64) - 1)]),
        'start': p.build_script_start(uid, 0xabcd, 2000, 1000, 0x3fff0000),
        'stop': p.build_script_stop_broadcast(0xabcd),
        'sync': p.build_time_sync_broadcast(0x3fff0000),
        'heartbeat': p.build_script_heartbeat_broadcast(0xabcd, 1000),
        'step': p.build_script_step(uid, 0xabcd, 2000),
        'otaBegin': p.build_ota_update_begin(uid, 0xabcd, 'main.py', 80, 80 * 255),
        'otaWrite': p.build_ota_update_write(uid, 0xabcd, 0, b'\xff' * 80),
        'otaEnd': p.build_ota_update_end(uid, 0xabcd),
        'otaAbort': p.build_ota_update_abort(uid, 0xabcd),
        'otaReboot': p.build_ota_update_reboot(uid),
        'chaseStart': p.build_script_start_chase(uid, 0xabcd, 0xa0d10bff, 0x0102, -40),
        'audioPlay': p.build_audio_play(0xa0d10bff, 0x0102, 1500),
        'audioStop': p.build_audio_stop(0xa0d10bff, 0x0102),
        'audioStatus': p.build_audio_status_request(0xa0d10bff),
    }
    destination = args.app / 'test/fixtures/master_golden.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({key: list(value) for key, value in frames.items()}, indent=2) + '\n')
    print('Wrote', len(frames), 'original NFCP frames to', destination)


if __name__ == '__main__':
    main()
