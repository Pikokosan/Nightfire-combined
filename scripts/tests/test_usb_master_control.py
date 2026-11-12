import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'main'))
import nfcp
import bridge_radio
from usb_master_control import MasterControl, decode_master, broadcast, KEEPALIVE_HOLD_MS
from usb_master_maintenance import BridgeMaintenance


class MasterTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.packets = []
        self.success = True
        self.master = MasterControl(self.send, lambda: self.now, lambda a, b: a - b)
        self.p = nfcp.NFCP(1)
        self.uid = 0x12ff5678

    def send(self, packet):
        self.packets.append(packet)
        return self.success

    def test_golden_broadcasts(self):
        for command, data in ((0x11, b''), (0x73, b'\0\0'), (0x74, b'\0' * 6), (0x75, b'\xff' * 4)):
            self.assertEqual(broadcast(command, data), self.p.build_broadcast(command, data))

    def test_all_ported_unique_commands(self):
        frames = [self.p.build_arm_unique(self.uid), self.p.build_disarm_unique(self.uid),
                  self.p.build_unique_fire(self.uid, 1 << 63),
                  self.p.build_set_group(self.uid, 65535), self.p.build_set_network(self.uid, 1),
                  self.p.build_set_unique(self.uid, 2), self.p.build_set_radio_channel(self.uid, 74),
                  self.p.build_ping(self.uid, b'TEST'), self.p.build_resistance_request(self.uid, True),
                  self.p.build_output_status_request(self.uid),
                  self.p.build_set_resistance_calibration(self.uid, 0, [(20, 47)]),
                  self.p.build_script_clear(self.uid, 1),
                  self.p.build_script_load(self.uid, 1, 0, [(0, 1 << 63)]),
                  self.p.build_script_start(self.uid, 1, 2000, 0, 5000),
                  self.p.build_script_stop(self.uid, 1), self.p.build_script_step(self.uid, 1, 0),
                  self.p.build_script_heartbeat(self.uid, 1, 0),
                  self.p.build_ota_update_begin(self.uid, 1, 'main.py', 80, 2000),
                  self.p.build_ota_update_write(self.uid, 1, 0, b'\xff' * 80),
                  self.p.build_ota_update_end(self.uid, 1), self.p.build_ota_update_abort(self.uid, 1),
                  self.p.build_ota_update_reboot(self.uid)]
        for frame in frames:
            self.assertIsNotNone(decode_master(frame), frame.hex())

    def test_unknown_commands_and_bad_lengths_rejected(self):
        no_data_allowed = {0x10, 0x11, 0x22, 0x24, 0x27, 0x29, 0x52, 0x64, 0x7f}
        for command in range(256):
            self.assertEqual(decode_master(self.p.build_unique_command(self.uid, command)) is not None,
                             command in no_data_allowed)
        for frame in (self.p.build_arm_broadcast(), self.p.build_group_fire(1, 1),
                      self.p.build_unique_fire(self.uid, 0),
                      self.p.build_script_load(self.uid, 1, 0, [])):
            self.assertIsNone(decode_master(frame))

    def test_crc_escaping_concatenation_and_source(self):
        frame = self.p.build_arm_unique(self.uid)
        broken = bytearray(frame)
        broken[-1] ^= 1
        for bad in (broken, frame[:-1], frame + frame, frame + b'\xff',
                    nfcp.NFCP(2).build_arm_unique(self.uid)):
            self.assertIsNone(decode_master(bad))

    def test_arm_requires_host_and_optional_interlock(self):
        frame = self.p.build_arm_unique(self.uid)
        self.assertFalse(self.master.permit(frame, False))
        self.assertFalse(self.master.permit(frame, True, 0))
        self.assertFalse(self.master.permit(frame, True, 1))
        self.assertTrue(self.master.permit(frame, True, 2))
        self.assertTrue(self.master.may_be_armed)

    def test_fire_requires_arm_and_maintenance_is_inhibited(self):
        fire = self.p.build_unique_fire(self.uid, 1)
        self.assertFalse(self.master.permit(fire, True))
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.assertTrue(self.master.permit(fire, True))
        self.assertFalse(self.master.permit(self.p.build_set_group(self.uid, 2), True))

    def test_fire_pulse_read_and_set_only_while_safe(self):
        read = self.p.build_unique_command(self.uid, 0x29)
        set_ms = self.p.build_unique_command(self.uid, 0x29, (250).to_bytes(2, 'big'))
        self.assertIsNone(decode_master(self.p.build_unique_command(self.uid, 0x29, b'\x01')))
        self.assertFalse(self.master.permit(set_ms, False))
        self.assertTrue(self.master.permit(read, True))
        self.assertTrue(self.master.permit(set_ms, True))
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.assertTrue(self.master.permit(read, True))
        self.assertFalse(self.master.permit(set_ms, True))

    def test_stop_and_disarm_allowed_without_host(self):
        for frame in (self.p.build_disarm_broadcast(), self.p.build_disarm_unique(self.uid),
                      self.p.build_script_stop_broadcast(), self.p.build_script_stop(self.uid)):
            self.assertTrue(self.master.permit(frame, False, 1))

    def test_expiry_sends_three_stop_disarm_pairs(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        for now in (0, 40, 80):
            self.now = now
            self.master.poll(False, True)
        self.assertEqual(self.packets, [b'\x04\xd2' + frame for _ in range(3) for frame in
                                      (self.p.build_script_stop_broadcast(), self.p.build_disarm_broadcast())])
        self.assertFalse(self.master.permit(self.p.build_arm_unique(self.uid), False))
        self.master.poll(True, True)
        self.assertFalse(self.master.may_be_armed)
        self.assertTrue(self.master.needs_rearm)
        self.assertTrue(self.master.permit(self.p.build_arm_unique(self.uid), True))
        self.assertFalse(self.master.needs_rearm)

    def drain_stop(self, host_active=True):
        for _ in range(3):
            self.master.poll(host_active, True)
            self.now += 40

    def test_new_session_without_arm_resumes_keepalive_after_stop(self):
        self.master.stop()
        self.assertTrue(self.master.needs_rearm)
        self.drain_stop()
        self.assertEqual(self.master.stop_pending, 0)
        self.master.poll(True, True)
        self.assertFalse(self.master.needs_rearm)

    def test_possibly_armed_stop_holds_keepalive_past_field_timeout(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.master.poll(False, True)
        self.now += 40
        self.drain_stop()
        self.now = KEEPALIVE_HOLD_MS - 1
        self.master.poll(True, True)
        self.assertTrue(self.master.needs_rearm)
        self.now = KEEPALIVE_HOLD_MS
        self.master.poll(True, True)
        self.assertFalse(self.master.needs_rearm)
        # Resuming the keepalive never re-permits fire without a fresh ARM.
        self.assertFalse(self.master.permit(self.p.build_unique_fire(self.uid, 1), True))

    def test_keepalive_stays_held_without_host(self):
        self.master.stop()
        self.drain_stop(host_active=False)
        self.now = KEEPALIVE_HOLD_MS * 2
        self.master.poll(False, True)
        self.assertTrue(self.master.needs_rearm)

    def disarm_ack(self, uid):
        payload = (uid & 0xFFFFFFFF).to_bytes(4, 'big') + bytes([0x50, 0x11, 0])
        body = bytes([len(payload)]) + payload
        body += bridge_radio.int_to_be(bridge_radio.crc16_ccitt(body), 2)
        frame = bytearray(b'\xff\xfa')
        for value in body:
            frame.append(value)
            if value == 255:
                frame.append(254)
        self.master.observe_reply(bytes(frame))

    def test_arm_state_safe_armed_then_confirmed_safe(self):
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_SAFE)
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_ARMED)
        self.disarm_ack(self.uid)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_SAFE)

    def test_broadcast_disarm_is_unconfirmed_until_acked(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.master.permit(self.p.build_disarm_broadcast(), True)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_UNCONFIRMED)
        self.disarm_ack(self.uid)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_SAFE)

    def test_host_loss_is_unconfirmed_through_the_self_disarm_window(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.master.poll(False, True)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_UNCONFIRMED)
        self.now += 40
        self.drain_stop()
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_UNCONFIRMED)
        self.now = KEEPALIVE_HOLD_MS
        self.master.poll(True, True)
        self.assertEqual(self.master.arm_state(), MasterControl.ARM_SAFE)

    def test_rf_failure_keeps_stop_pending(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.success = False
        self.master.poll(False, True)
        self.now = 100
        self.master.poll(True, True)
        self.assertEqual(self.master.stop_pending, 3)
        self.assertFalse(self.master.permit(self.p.build_arm_unique(self.uid), True))

    def test_switch_safe_and_radio_fault_trigger_stop(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.master.poll(True, False)
        self.assertEqual(self.master.stop_pending, 3)
        self.master.poll(True, True, 1)
        self.assertEqual(len(self.packets), 2)

    def test_only_correlated_disarm_ack_clears_possible_arm(self):
        self.master.permit(self.p.build_arm_unique(self.uid), True)
        self.master.sent(self.p.build_disarm_broadcast())
        self.assertTrue(self.master.may_be_armed)
        self.master.observe_reply(self.p.build_ack(self.uid + 1, 0x11))
        self.assertTrue(self.master.may_be_armed)
        self.master.observe_reply(self.p.build_ack(self.uid, 0x10))
        self.assertTrue(self.master.may_be_armed)
        self.master.observe_reply(self.p.build_ack(self.uid, 0x11))
        self.assertFalse(self.master.may_be_armed)


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        import os
        os.chdir(self.temp.name)
        self.maintenance = BridgeMaintenance(None, SimpleNamespace())

    def tearDown(self):
        import os
        self.maintenance.abort()
        os.chdir(self.previous)
        self.temp.cleanup()

    def begin(self, data, crc=None):
        name = b'main.py'
        self.maintenance.file_command(0x32, bytes([len(name)]) + name +
            len(data).to_bytes(4, 'big') + (bridge_radio.crc16_ccitt(data) if crc is None else crc).to_bytes(2, 'big'))

    def test_file_verified_before_replace_and_original_preserved(self):
        Path('main.py').write_bytes(b'original')
        data = b'replacement'
        self.begin(data)
        self.maintenance.file_command(0x33, b'\0' * 4 + data)
        self.assertEqual(Path('main.py').read_bytes(), b'original')
        self.maintenance.file_command(0x34, b'')
        self.assertEqual(Path('main.py').read_bytes(), data)
        self.assertEqual(Path('main.py.bak').read_bytes(), b'original')

    def test_bad_checksum_never_replaces_file(self):
        Path('main.py').write_bytes(b'original')
        self.begin(b'bad', crc=0)
        self.maintenance.file_command(0x33, b'\0' * 4 + b'bad')
        with self.assertRaises(ValueError):
            self.maintenance.file_command(0x34, b'')
        self.assertEqual(Path('main.py').read_bytes(), b'original')

    def test_offset_and_path_rejected(self):
        self.begin(b'test')
        with self.assertRaises(ValueError):
            self.maintenance.file_command(0x33, b'\0\0\0\1test')
        name = b'../x.py'
        with self.assertRaises(ValueError):
            self.maintenance.file_command(0x32, bytes([len(name)]) + name + b'\0' * 6)

    def test_radio_save_preserves_interlock(self):
        Path('nrf_bridge_config.py').write_text('original')
        self.maintenance.config = SimpleNamespace(ARM_SWITCH_PIN=9, STATUS_LED_PIN=10,
                                                  STATUS_LED_BRIGHTNESS=40)
        self.maintenance.save(1234, 90)
        content = Path('nrf_bridge_config.py').read_text()
        self.assertIn('ARM_SWITCH_PIN = 9', content)
        self.assertIn('STATUS_LED_BRIGHTNESS = 40', content)
        self.assertIn('MASTER_ENABLED = True', content)
        self.assertEqual(Path('nrf_bridge_config.py.bak').read_text(), 'original')

    def install(self, name, data):
        encoded = name.encode()
        self.maintenance.file_command(0x32, bytes([len(encoded)]) + encoded +
            len(data).to_bytes(4, 'big') + bridge_radio.crc16_ccitt(data).to_bytes(2, 'big'))
        self.maintenance.file_command(0x33, b'\0' * 4 + data)
        self.maintenance.file_command(0x34, b'')

    def test_bundle_update_is_marked_for_trial_and_confirmed(self):
        Path('usb_master_bundle.mpy').write_bytes(b'old')
        Path('usb_master_bundle.mpy.bak').write_bytes(b'older')
        self.install('usb_master_bundle.mpy', b'new')
        self.assertEqual(Path('usb_master_bundle.mpy').read_bytes(), b'new')
        self.assertEqual(Path('usb_master_bundle.mpy.bak').read_bytes(), b'old')
        self.assertTrue(Path('update_pending').exists())
        Path('update_pending').rename('update_trial')  # what main.py does at boot
        self.assertTrue(self.maintenance.confirm_trial())
        self.assertFalse(Path('update_trial').exists())
        self.assertFalse(self.maintenance.confirm_trial())

    def test_resent_chunk_is_acknowledged_without_writing_twice(self):
        Path('main.py').write_bytes(b'original')
        self.begin(b'abcdef')
        self.maintenance.file_command(0x33, b'\0' * 4 + b'abc')
        self.maintenance.file_command(0x33, b'\0' * 4 + b'abc')  # lost reply
        self.maintenance.file_command(0x33, b'\0\0\0\3' + b'def')
        self.maintenance.file_command(0x34, b'')
        self.assertEqual(Path('main.py').read_bytes(), b'abcdef')

    def test_other_files_do_not_start_a_trial(self):
        self.install('main.py', b'entry')
        self.assertFalse(Path('update_pending').exists())

    def test_storage_is_checked_before_writing(self):
        with patch('usb_master_maintenance._free_bytes', return_value=1000):
            with self.assertRaises(ValueError):
                self.install('usb_master_bundle.mpy', b'x' * 2000)
        self.assertFalse(Path('usb_master_bundle.mpy.tmp').exists())

    def test_failed_write_removes_partial_copy(self):
        Path('main.py').write_bytes(b'original')
        self.begin(b'abcdef')

        class FullFile:
            def write(self, data):
                raise OSError(28)

            def close(self):
                pass

        self.maintenance.transfer['file'].close()
        self.maintenance.transfer['file'] = FullFile()
        with self.assertRaises(OSError):
            self.maintenance.file_command(0x33, b'\0' * 4 + b'abc')
        self.assertIsNone(self.maintenance.transfer)
        self.assertFalse(Path('main.py.tmp').exists())
        self.assertEqual(Path('main.py').read_bytes(), b'original')

    def test_zero_statvfs_means_unknown_not_full(self):
        import usb_master_maintenance
        with patch('os.statvfs', create=True, return_value=(0,) * 9 + (512,)):
            self.assertGreater(usb_master_maintenance._free_bytes(), 256 * 1024)
        with patch('os.statvfs', create=True, return_value=(4096, 4096, 16, 2, 2, 0, 0, 0, 0, 255)):
            self.assertEqual(usb_master_maintenance._free_bytes(), 8192)


class BootEntryTests(unittest.TestCase):
    """Runs the generated main.py against a temporary filesystem."""

    def setUp(self):
        import os
        self.temp = tempfile.TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.temp.name)
        self.resets = []
        sys.modules['machine'] = SimpleNamespace(reset=lambda: self.resets.append(1))
        sys.path.insert(0, self.temp.name)

    def tearDown(self):
        import os
        sys.path.remove(self.temp.name)
        sys.modules.pop('machine', None)
        sys.modules.pop('usb_master_bundle', None)
        os.chdir(self.previous)
        self.temp.cleanup()

    def boot(self, bundle_source):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "build"))
        from build_usb_master_bundle import ENTRY
        sys.modules.pop('usb_master_bundle', None)
        # A .py stands in for the .mpy; the entry only renames the .mpy names.
        Path('usb_master_bundle.py').write_text(bundle_source)
        exec(compile(ENTRY, 'main.py', 'exec'), {'__name__': '__main__'})

    def test_pending_bundle_boots_on_trial(self):
        Path('update_pending').write_text('1')
        self.boot('started = True\n')
        self.assertTrue(Path('update_trial').exists())
        self.assertFalse(self.resets)

    def test_unconfirmed_trial_is_rolled_back_at_next_boot(self):
        Path('usb_master_bundle.mpy').write_bytes(b'new')
        Path('usb_master_bundle.mpy.bak').write_bytes(b'old')
        Path('update_trial').write_text('1')
        self.boot('started = True\n')
        self.assertEqual(Path('usb_master_bundle.mpy').read_bytes(), b'old')
        self.assertFalse(Path('usb_master_bundle.mpy.bak').exists())
        self.assertFalse(Path('update_trial').exists())

    def test_trial_that_fails_to_import_rolls_back_and_resets(self):
        Path('usb_master_bundle.mpy').write_bytes(b'new')
        Path('usb_master_bundle.mpy.bak').write_bytes(b'old')
        Path('update_pending').write_text('1')
        with self.assertRaises(ValueError):
            self.boot('raise ValueError("incompatible .mpy file")\n')
        self.assertEqual(Path('usb_master_bundle.mpy').read_bytes(), b'old')
        self.assertEqual(self.resets, [1])

    def test_confirmed_bundle_failure_is_not_rolled_back(self):
        Path('usb_master_bundle.mpy').write_bytes(b'current')
        Path('usb_master_bundle.mpy.bak').write_bytes(b'old')
        with self.assertRaises(ValueError):
            self.boot('raise ValueError("config error")\n')
        self.assertEqual(Path('usb_master_bundle.mpy').read_bytes(), b'current')
        self.assertFalse(self.resets)


if __name__ == '__main__':
    unittest.main()
