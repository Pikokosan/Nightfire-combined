import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/controller/system_arm_status.dart';
import 'package:nightfire_controller/protocol/interface_status.dart';

import 'auto_continuity_test.dart' show HeartbeatRadio;

InterfaceStatus key(int position, {int faults = 0, int? pin = 6}) =>
    InterfaceStatus(position, 0, 1, faults, 0, 3000, pin, null, 4, 0);

void main() {
  late RadioSession session;

  setUp(() {
    session = RadioSession(HeartbeatRadio())
      ..ready = true
      ..values['Firmware'] = 'usb-master-0.4.3';
  });
  tearDown(() => session.dispose());

  SystemArmStatus state() => SystemArmStatus.from(session);

  test('offline: unknown', () {
    session.ready = false;
    expect(state().level, SystemArmLevel.unknown);
    expect(state().label, 'ARM STATE UNKNOWN');
  });

  test('key SAFE, nothing armed: system safe', () {
    session.interfaceStatus = key(1);
    expect(state().level, SystemArmLevel.safe);
    expect(state().label, 'SYSTEM SAFE');
    expect(state().error, isNull);
  });

  test('only the key armed: command module armed', () {
    session.interfaceStatus = key(2);
    expect(state().level, SystemArmLevel.commandArmed);
    expect(state().label, 'COMMAND MODULE ARMED');
  });

  test('key and modules armed: system armed', () {
    session.interfaceStatus = key(2);
    session.master.armed.addAll([1, 2]);
    expect(state().level, SystemArmLevel.systemArmed);
    expect(state().label, 'SYSTEM ARMED • 2 modules');
  });

  test('a bridge without a key switch: modules alone arm the system', () {
    session.interfaceStatus = key(0, pin: null);
    session.master.armed.add(1);
    expect(state().label, 'SYSTEM ARMED • 1 module');
    session.master.armed.clear();
    expect(state().label, 'SYSTEM SAFE');
  });

  group('errors', () {
    test('key on at start-up', () {
      session.interfaceStatus = key(
        2,
        faults: InterfaceStatus.keyLatchedFault,
      );
      expect(state().level, SystemArmLevel.error);
      expect(state().label, 'ARM KEY ERROR');
      expect(state().error, contains('Turn it to SAFE'));
    });

    test('command module faults are named', () {
      session.interfaceStatus = key(2, faults: 8 | 16);
      expect(state().label, 'COMMAND MODULE FAULT');
      expect(
        state().error,
        'Radio unavailable, RF heartbeat to the modules failing.',
      );
      // The LED fault alone is not an arming problem.
      session.interfaceStatus = key(1, faults: 2);
      expect(state().level, SystemArmLevel.safe);
    });

    test('switch reading unknown', () {
      session.interfaceStatus = key(0);
      expect(state().label, 'ARM KEY UNKNOWN');
    });

    test('modules armed while the key is SAFE', () {
      session.interfaceStatus = key(1);
      session.master.armed.add(1);
      expect(state().level, SystemArmLevel.error);
      expect(state().error, contains('DISARM ALL'));
    });

    test('after STOP until DISARM ALL', () {
      session.interfaceStatus = key(1);
      session.master.interrupted = true;
      expect(state().label, 'STOPPED');
    });

    test('a failed arm is shown with the arm state', () async {
      session.interfaceStatus = key(2, faults: InterfaceStatus.keyLatchedFault);
      expect(await session.master.armAll(), isFalse);
      session.interfaceStatus = key(2);
      expect(state().label, 'COMMAND MODULE ARMED');
      expect(state().error, startsWith('Arm all modules not confirmed'));
    });
  });
}
