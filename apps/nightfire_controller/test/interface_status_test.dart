import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/interface_status.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

class InterfaceRadio extends FieldRadio {
  int heartbeats = 0;
  bool rejectHeartbeat = false;
  int dropHeartbeatReplies = 0;
  bool masterHeartbeat = false;
  bool groupSettings = false;
  int keepaliveStatusQueries = 0;

  @override
  Future<void> write(Uint8List bytes) async {
    final request = UsbLineDecoder().add(bytes).single;
    var payload = <int>[];
    var flags = 2;
    if (request.command == 3) {
      final version = ascii.encode('usb-monitor-0.3');
      payload = [1, 2, 3, 4, 0, 0, 4, 210, version.length, ...version];
    } else if (request.command == 0x18) {
      heartbeats++;
      if (dropHeartbeatReplies > 0) {
        dropHeartbeatReplies--;
        return; // Reply lost in transit.
      }
      if (rejectHeartbeat) {
        flags = 6;
        payload = [5];
      }
    } else if (request.command == 0x17) {
      payload = [
        1,
        8 | (masterHeartbeat ? 16 : 0) | (groupSettings ? 32 : 0),
        0,
        0,
        1,
        0,
        0,
        0,
        11,
        184,
        255,
        255,
        4,
        0,
        0,
      ];
    } else if (request.command == 0x19) {
      keepaliveStatusQueries++;
      payload = [1, 1, 9, 196, 0, 10, 0, 0, 0, 4, 0, 0, 0, 0];
    } else {
      return super.write(bytes);
    }
    stream.add(
      usbLine(
        BridgeFrame(request.sequence, request.command, payload, flags: flags),
      ),
    );
  }
}

void main() {
  test('RF heartbeat telemetry is capability-gated and continues during field waits', () async {
    final radio = InterfaceRadio()..masterHeartbeat = true;
    final session = RadioSession(
      radio,
      discoveryRounds: 1,
      discoveryInterval: Duration.zero,
    );
    await session.connect('field');
    expect(session.masterHeartbeatStatus!.sent, 4);
    expect(session.masterHeartbeatStatus!.intervalMs, 2500);
    await session.discoverModules();
    radio.holdMeasurement = true;
    final pending = session.readModule(FieldRadio.uid, resistance: true);
    await Future<void>.delayed(const Duration(milliseconds: 1100));
    expect(radio.keepaliveStatusQueries, greaterThanOrEqualTo(2));
    await session.disconnect();
    await pending;
    expect(session.masterHeartbeatStatus, isNull);
    expect(() => MasterHeartbeatStatus.parse([1, 1]), throwsFormatException);
    session.dispose();
  });

  test('Unconfigured hardware is explicit and never described as armed', () {
    final status = InterfaceStatus.parse([
      1,
      8,
      0,
      0,
      1,
      0,
      0,
      5,
      11,
      184,
      255,
      255,
      4,
      0,
      0,
    ]);
    expect(status.switchText, 'Not configured');
    expect(status.ledText, 'Not configured');
    expect(status.timeoutMs, 3000);
    expect(status.sessionState, 1);
    expect(() => InterfaceStatus.parse([1, 8]), throwsFormatException);
    expect(
      () => InterfaceStatus.parse([
        2,
        8,
        0,
        0,
        1,
        0,
        0,
        0,
        11,
        184,
        255,
        255,
        4,
        0,
        0,
      ]),
      throwsFormatException,
    );
  });
  test('An ARM switch position does not claim remote armed confirmation', () {
    final status = InterfaceStatus.parse([
      1,
      9,
      2,
      0,
      1,
      0,
      0,
      0,
      11,
      184,
      6,
      255,
      4,
      0,
      0,
    ]);
    expect(status.switchText, contains('controls disabled'));
  });
  test('Version 0.3 connects, queries modules and keeps heartbeats alive during field waits', () async {
    final radio = InterfaceRadio();
    final session = RadioSession(
      radio,
      discoveryRounds: 1,
      discoveryInterval: Duration.zero,
    );
    await session.connect('field');
    await session.discoverModules();
    expect(session.canQueryModules, isTrue);
    expect(session.interfaceStatus!.sessionState, 1);
    radio.holdMeasurement = true;
    final pending = session.readModule(FieldRadio.uid, resistance: true);
    await Future<void>.delayed(const Duration(milliseconds: 1100));
    expect(radio.heartbeats, greaterThanOrEqualTo(2));
    await session.disconnect();
    await pending;
    expect(session.interfaceStatus, isNull);
    session.dispose();
  });
  test('A single lost heartbeat reply does not end the session', () async {
    final radio = InterfaceRadio();
    final session = RadioSession(radio);
    await session.connect('field');
    radio.dropHeartbeatReplies = 1;
    await Future<void>.delayed(const Duration(milliseconds: 2600));
    expect(session.ready, isTrue);
    expect(session.heartbeatMisses, 0);
    expect(radio.heartbeats, greaterThanOrEqualTo(3));
    session.dispose();
  });

  test('Consecutive lost heartbeat replies end the session', () async {
    final radio = InterfaceRadio();
    final session = RadioSession(radio);
    await session.connect('field');
    radio.dropHeartbeatReplies = 1000;
    await Future<void>.delayed(const Duration(milliseconds: 3200));
    expect(session.ready, isFalse);
    expect(session.status, contains('heartbeat lost'));
    session.dispose();
  });

  test(
    'Heartbeat failure invalidates the interface and cancels its timer',
    () async {
      final radio = InterfaceRadio();
      final session = RadioSession(radio);
      await session.connect('field');
      radio.rejectHeartbeat = true;
      await Future<void>.delayed(const Duration(milliseconds: 1100));
      expect(session.ready, isFalse);
      expect(session.interfaceStatus, isNull);
      expect(session.status, contains('heartbeat lost'));
      session.dispose();
    },
  );
}
