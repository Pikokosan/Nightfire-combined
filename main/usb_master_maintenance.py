"""Radio maintenance for the opt-in USB master firmware."""
import os
import bridge_radio

BUNDLE_FILE = 'usb_master_bundle.mpy'
TRIAL_PENDING = 'update_pending'
TRIAL_ACTIVE = 'update_trial'
UPDATABLE_FILES = ('main.py', BUNDLE_FILE, 'bridge_radio.py', 'nrf_bridge_config.py',
                   'usb_master_control.py', 'usb_master_maintenance.py',
                   'usb_bridge_transport.py', 'usb_monitor_nfcp.py', 'command_interface.py')


def _remove(name):
    try:
        os.remove(name)
        return True
    except OSError:
        return False


def _free_bytes():
    try:
        stat = os.statvfs('/')
    except (AttributeError, OSError):
        return 1 << 30  # Host tests and ports without statvfs.
    if not stat[0] or not stat[2]:
        # The nice!nano build reports all zeros; size unknown, so let the
        # write itself fail if storage runs out (the old file stays intact).
        return 1 << 30
    return stat[0] * stat[3]


class BridgeMaintenance:
    def __init__(self, radio, config):
        self.radio = radio
        self.config = config
        self.transfer = None

    def save(self, network, channel):
        # Preserve interlock and LED settings when radio settings change.
        names = ('RADIO_ADDRESS', 'RADIO_TX_POWER', 'ARM_SWITCH_PIN',
                 'STATUS_LED_PIN', 'STATUS_LED_COUNT', 'STATUS_LED_ORDER',
                 'STATUS_LED_TIMING_NS')
        with open('nrf_bridge_config.py.tmp', 'w') as output:
            output.write('MASTER_ENABLED = True\nNETWORK_ID = %d\nRADIO_CHANNEL = %d\n' %
                         (network, channel))
            for name in names:
                if hasattr(self.config, name):
                    output.write('%s = %r\n' % (name, getattr(self.config, name)))
        try:
            os.remove('nrf_bridge_config.py.bak')
        except OSError:
            pass
        os.rename('nrf_bridge_config.py', 'nrf_bridge_config.py.bak')
        os.rename('nrf_bridge_config.py.tmp', 'nrf_bridge_config.py')

    def scan(self, channel):
        results = []
        try:
            for candidate in (0,) + tuple(range(74, 101)):
                sample = self.radio.noise_sample(candidate, duration_ms=45, interval_ms=3)
                avg = int(sample.get('avg', 0))
                low = int(sample.get('min', 0))
                high = int(sample.get('max', 0))
                count = int(sample.get('count', 0))
                busy = int(sample.get('busy_count', count if high >= -85 else 0))
                score = max(0, min(100, (-85 - high) * 4 + (-85 - avg) * 2 - busy * 5))
                results.append((score, candidate, avg, low, high, count, busy))
        finally:
            self.radio.channel(channel)
        results.sort(reverse=True)
        payload = bytearray([min(10, len(results))])
        for score, candidate, avg, low, high, count, busy in results[:10]:
            payload.append(candidate)
            for value in (avg, low, high):
                payload.extend(bridge_radio.int_to_be(value & 65535, 2))
            payload.extend(bytes([min(count, 255), min(busy, 255), score]))
        return payload

    def confirm_trial(self):
        """Keep a freshly installed bundle once the app holds a session."""
        _remove(TRIAL_PENDING)
        return _remove(TRIAL_ACTIVE)

    def abort(self):
        if self.transfer:
            try:
                self.transfer['file'].close()
                os.remove(self.transfer['name'] + '.tmp')
            except OSError:
                pass
            self.transfer = None

    def file_command(self, command, payload):
        if command == 0x32:
            if not payload or len(payload) != payload[0] + 7:
                raise ValueError('Invalid begin length')
            name = bytes(payload[1:1 + payload[0]]).decode()
            if name not in UPDATABLE_FILES:
                raise ValueError('Unsupported bridge filename')
            size = bridge_radio.int_from_be(payload[-6:-2])
            if size < 1 or size > 256 * 1024:
                raise ValueError('Invalid file size')
            self.abort()
            # The nice!nano filesystem is about 64 KiB. Drop the previous
            # backup first so only the running copy and the new one coexist;
            # the running copy still becomes the backup at END.
            _remove(name + '.bak')
            if _free_bytes() < size + 2048:
                raise ValueError('Not enough bridge storage for this file')
            self.transfer = dict(name=name, size=size, written=0, crc=65535,
                                 expected=bridge_radio.int_from_be(payload[-2:]),
                                 file=open(name + '.tmp', 'wb'))
        elif command == 0x33:
            current = self.transfer
            if current is None or len(payload) < 5:
                raise ValueError('No bridge transfer')
            offset = bridge_radio.int_from_be(payload[:4])
            chunk = payload[4:]
            if offset < current['written'] and offset + len(chunk) <= current['written']:
                return  # Retry after a lost USB reply; already written.
            if offset != current['written'] or offset + len(chunk) > current['size']:
                raise ValueError('Invalid bridge transfer offset')
            try:
                current['file'].write(chunk)
            except OSError:
                self.abort()  # Free the partial copy at once (e.g. storage full).
                raise
            current['written'] += len(chunk)
            current['crc'] = bridge_radio.crc16_ccitt(chunk, current['crc'])
        elif command == 0x34:
            current = self.transfer
            if payload or current is None:
                raise ValueError('No bridge transfer')
            if current['written'] != current['size'] or current['crc'] != current['expected']:
                self.abort()
                raise ValueError('Bridge transfer checksum/length mismatch')
            current['file'].close()
            name = current['name']
            _remove(name + '.bak')
            try:
                os.rename(name, name + '.bak')
            except OSError:
                pass
            os.rename(name + '.tmp', name)
            self.transfer = None
            if name == BUNDLE_FILE:
                # main.py boots the new bundle once on trial; it is kept only
                # after a host session proves USB control works (confirm_trial).
                with open(TRIAL_PENDING, 'w') as marker:
                    marker.write('1')
        else:
            raise ValueError('Unsupported file command')
