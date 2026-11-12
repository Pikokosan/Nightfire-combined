import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/interface_status.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/widgets/fire_pulse_setting.dart';

import 'master_controller_test.dart' show MasterRadio;

/// Field modules that answer CMD_FIRE_PULSE (0x29) like field-module 0.3.1.
class PulseRadio extends MasterRadio {
  final pulses = <int, int>{};
  final old = <int>{}; // firmware before 0.3.1: no reply
  final busy = <int>{};
  final sets = <int, List<int>>{};

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    final packet = request.command == 0x10 ? decodeNfcp(request.payload) : null;
    if (packet == null || packet.command != 0x29 || packet.target == null) {
      return super.write(bytes);
    }
    bridge.add(request);
    stream.add(
      usbLine(BridgeFrame(request.sequence, request.command, [0], flags: 2)),
    );
    final uid = packet.target!;
    if (old.contains(uid)) return;
    if (packet.data.isNotEmpty) {
      if (busy.contains(uid)) {
        reply(uid, 0x51, [0x29, 5]);
        return;
      }
      final value = readUnsigned(packet.data, 0, 2);
      sets.putIfAbsent(uid, () => []).add(value);
      pulses[uid] = value;
    }
    reply(uid, 0x2a, bigEndian(pulses[uid] ?? 5, 2));
  }
}

const _status = InterfaceStatus(
  1,
  0,
  1,
  0,
  0,
  3000,
  11,
  6,
  4,
  0,
  capabilities: 128 | 64 | 8,
);

void main() {
  const first = 0x12ff5678, second = 0x0badf00d;
  late PulseRadio radio;
  late RadioSession session;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    radio = PulseRadio();
    session = RadioSession(
      radio,
      moduleTimeout: const Duration(milliseconds: 40),
    );
    session.ready = true;
    // Plain 0x10 relay (the fake radio has no reliable 0x1A delivery);
    // capability 128 is what enables the hold-time command.
    session.values['Firmware'] = 'usb-master-0.4.0';
    session.interfaceStatus = _status;
    session.modules[first] = FieldModule(first, 1, 24, 0x20)..seenInScan = true;
    session.modules[second] = FieldModule(second, 2, 24, 0x20)
      ..seenInScan = true;
  });
  tearDown(() => session.dispose());

  test('sends the hold time to every discovered module', () async {
    expect(await session.master.pushFirePulse(250), isTrue);
    expect(radio.sets, {
      first: [250],
      second: [250],
    });
    expect(session.modules[first]!.firePulseMs, 250);
    expect(session.modules[second]!.firePulseError, isNull);
  });

  test('names the modules that did not confirm', () async {
    radio.old.add(second);
    radio.busy.add(first);
    expect(await session.master.pushFirePulse(100), isFalse);
    expect(session.master.lastError, contains('2 module(s)'));
    expect(session.modules[first]!.firePulseError, contains('Busy'));
    expect(session.modules[second]!.firePulseError, contains('0.3.1'));
  });

  test('refused while armed, out of range or on an older bridge', () async {
    session.master.armed.add(first);
    expect(await session.master.pushFirePulse(50), isFalse);
    session.master.armed.clear();
    expect(await session.master.pushFirePulse(0), isFalse);
    expect(await session.master.pushFirePulse(2001), isFalse);
    session.interfaceStatus = const InterfaceStatus(
      1,
      0,
      1,
      0,
      0,
      3000,
      11,
      6,
      4,
      0,
      capabilities: 64 | 8,
    );
    expect(await session.master.pushFirePulse(50), isFalse);
    expect(session.master.lastError, contains('usb-master-0.4.5'));
    expect(radio.sets, isEmpty);
  });

  testWidgets('settings field saves the value and sends it', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: FirePulseSetting(session: session)),
      ),
    );
    expect(find.text('5'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('fire-pulse-ms')), '3000');
    await tester.pump();
    expect(find.textContaining('Enter 1-2000'), findsOneWidget);
    await tester.enterText(find.byKey(const ValueKey('fire-pulse-ms')), '40');
    await tester.pump();
    expect(session.firePulseMs, 40);
    await tester.runAsync(() async {
      await tester.tap(find.byKey(const ValueKey('fire-pulse-send')));
      await Future<void>.delayed(const Duration(milliseconds: 20));
      for (var i = 0; i < 50 && session.busy; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
    });
    await tester.pump();
    expect(radio.sets, {
      first: [40],
      second: [40],
    });
    expect(find.textContaining('40 ms'), findsNWidgets(2));
  });
}
