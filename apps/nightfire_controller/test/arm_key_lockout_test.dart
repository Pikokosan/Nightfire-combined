import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/interface_status.dart';
import 'package:nightfire_controller/widgets/arm_key_dialog.dart';

import 'auto_continuity_test.dart' show HeartbeatRadio;

InterfaceStatus status({int position = 2, int faults = 0}) =>
    InterfaceStatus(position, 0, 1, faults, 0, 3000, 6, null, 4, 0);

void main() {
  group('session', () {
    late HeartbeatRadio radio;
    late RadioSession session;

    Future<void> heartbeats(int count) async {
      final target = radio.heartbeats + count;
      while (radio.heartbeats < target) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      await Future<void>.delayed(const Duration(milliseconds: 100));
    }

    Future<void> connect({required int switchPosition}) async {
      radio = HeartbeatRadio()..switchPosition = switchPosition;
      session = RadioSession(radio, moduleTimeout: const Duration(milliseconds: 300));
      session.autoContinuity = false;
      await session.connect('field');
      await heartbeats(1);
    }

    tearDown(() => session.dispose());

    test('key on ARM when the app connects locks arming until SAFE', () async {
      await connect(switchPosition: 2);
      expect(session.armKeyLockout, isTrue);
      // Arming is refused with the reason, before any radio traffic.
      session.values['Firmware'] = 'usb-master-0.4.3';
      expect(session.master.canArm, isFalse);
      expect(await session.master.armAll(), isFalse);
      expect(session.master.status, contains('Turn it to SAFE'));
      // Still ARM on later heartbeats: still locked.
      await heartbeats(2);
      expect(session.armKeyLockout, isTrue);
      radio.switchPosition = 1;
      await heartbeats(1);
      expect(session.armKeyLockout, isFalse);
      // Turning the key to ARM now is a deliberate arm: no lockout.
      radio.switchPosition = 2;
      await heartbeats(1);
      expect(session.armKeyLockout, isFalse);
    });

    test('key on SAFE when the app connects: no lockout', () async {
      await connect(switchPosition: 1);
      radio.switchPosition = 2;
      await heartbeats(1);
      expect(session.armKeyLockout, isFalse);
    });

    test('the bridge\'s own latch (power-on ARM) also locks arming', () async {
      await connect(switchPosition: 1);
      radio
        ..switchPosition = 2
        ..faults = InterfaceStatus.keyLatchedFault;
      await heartbeats(1);
      expect(session.armKeyLockout, isTrue);
      radio
        ..switchPosition = 1
        ..faults = 0;
      await heartbeats(1);
      expect(session.armKeyLockout, isFalse);
    });

    test('a new connection starts with a fresh latch', () async {
      await connect(switchPosition: 2);
      expect(session.armKeyLockout, isTrue);
      await session.disconnect();
      expect(session.armKeyLockout, isFalse);
    });
  });

  test('status text names the start-up latch', () {
    expect(
      status(faults: InterfaceStatus.keyLatchedFault).switchText,
      contains('turn to SAFE'),
    );
    expect(status().armKeyLatched, isFalse);
  });

  testWidgets('the popup explains, and closes once the key is on SAFE', (
    tester,
  ) async {
    final session = RadioSession(HeartbeatRadio());
    addTearDown(session.dispose);
    session.ready = true;
    session.interfaceStatus = status(faults: InterfaceStatus.keyLatchedFault);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showDialog<bool>(
              context: context,
              barrierDismissible: false,
              builder: (_) => ArmKeyDialog(session: session),
            ),
            child: const Text('open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('arm-key-dialog')), findsOneWidget);
    expect(find.text('Turn the ARM key off'), findsOneWidget);
    // Tapping outside does not dismiss it.
    await tester.tapAt(const Offset(5, 5));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('arm-key-dialog')), findsOneWidget);
    // Key turned to SAFE: it closes by itself.
    session.interfaceStatus = status(position: 1);
    session.notifyListeners();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('arm-key-dialog')), findsNothing);
    expect(MasterController.armKeyLockoutMessage, contains('SAFE'));
  });
}
