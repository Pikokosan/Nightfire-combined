import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

/// A bridge with the host heartbeat (0.3 monitor firmware) on top of the
/// field module replies of [FieldRadio].
class HeartbeatRadio extends FieldRadio {
  int switchPosition = 1; // SAFE
  int heartbeats = 0;

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    List<int>? payload;
    if (request.command == 3) {
      final version = ascii.encode('usb-monitor-0.3.1');
      payload = [1, 2, 3, 4, 0, 0, 4, 210, version.length, ...version];
    } else if (request.command == 0x18) {
      heartbeats++;
      payload = [];
    } else if (request.command == 0x17) {
      // version, caps (switch + actuation-disabled), switch, leds, session,
      // faults, age, timeout, switch pin, led pin, pixels, rejected.
      payload = [1, 9, switchPosition, 0, 1, 0, 0, 0, 11, 184, 6, 255, 4, 0, 0];
    }
    if (payload == null) {
      // FieldRadio decodes the same (complete) line again.
      return super.write(bytes);
    }
    stream.add(
      usbLine(
        BridgeFrame(request.sequence, request.command, payload, flags: 2),
      ),
    );
  }
}

void main() {
  late HeartbeatRadio radio;
  late RadioSession session;

  setUp(() async {
    radio = HeartbeatRadio();
    session = RadioSession(
      radio,
      discoveryRounds: 1,
      discoveryInterval: Duration.zero,
      moduleTimeout: const Duration(milliseconds: 300),
    );
    await session.connect('field');
    expect(session.ready, isTrue);
  });
  tearDown(() => session.dispose());

  Future<void> heartbeats(int count) async {
    final target = radio.heartbeats + count;
    while (radio.heartbeats < target) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    }
    await Future<void>.delayed(const Duration(milliseconds: 100));
  }

  test('nothing is measured automatically before discovery', () async {
    await heartbeats(2);
    expect(radio.measurementTargets, isEmpty);
  });

  test(
    'after discovery each heartbeat measures a module and flashes it',
    () async {
      await session.discoverModules();
      final module = session.modules[FieldRadio.uid]!;
      expect(module.resistance, isNull);
      final status = session.status;
      await heartbeats(2);
      expect(radio.measurementTargets, isNotEmpty);
      expect(radio.measurementTargets.toSet(), {FieldRadio.uid});
      expect(module.resistance, isNotNull);
      expect(module.measuredAt, isNotNull);
      expect(module.replyFlash, greaterThan(0));
      // Background checks leave the status line alone.
      expect(session.status, status);
    },
  );

  test(
    'no automatic checks while the switch is on ARM or turned off',
    () async {
      await session.discoverModules();
      radio.switchPosition = 2;
      await heartbeats(1); // the status read on this beat sees ARM
      radio.measurementTargets.clear();
      await heartbeats(2);
      expect(radio.measurementTargets, isEmpty);
      radio.switchPosition = 1;
      session.autoContinuity = false;
      await heartbeats(2);
      expect(radio.measurementTargets, isEmpty);
    },
  );

  test('a module that does not answer turns red until it answers', () async {
    await session.discoverModules();
    final module = session.modules[FieldRadio.uid]!;
    radio.silentUids.add(FieldRadio.uid);
    while (!module.noReply) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    }
    expect(module.error, contains('Automatic continuity check'));
    final flashes = module.replyFlash;
    radio.silentUids.clear();
    while (module.noReply) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    }
    expect(module.replyFlash, greaterThan(flashes));
    expect(module.error, isNull);
  });

  test('a manual check with no reply also turns it red', () async {
    await session.discoverModules();
    session.autoContinuity = false;
    radio.silentUids.add(FieldRadio.uid);
    await session.readModule(FieldRadio.uid, resistance: true);
    expect(session.modules[FieldRadio.uid]!.noReply, isTrue);
    radio.silentUids.clear();
    await session.readModule(FieldRadio.uid, resistance: true);
    expect(session.modules[FieldRadio.uid]!.noReply, isFalse);
  });

  test('a user query waits for the automatic check in flight', () async {
    await session.discoverModules();
    radio.holdMeasurement = true; // the automatic check will time out
    while (!session.autoContinuityRunning) {
      await Future<void>.delayed(const Duration(milliseconds: 20));
    }
    await session.readModule(FieldRadio.uid);
    final module = session.modules[FieldRadio.uid]!;
    // The info query got its own reply slot after the check finished.
    expect(module.info, isNotEmpty);
    expect(module.error, isNull);
    expect(session.autoContinuityRunning, isFalse);
  });
}
