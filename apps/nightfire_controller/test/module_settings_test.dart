import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/scripts/script_document.dart';
import 'package:nightfire_controller/widgets/module_battery.dart';
import 'package:nightfire_controller/widgets/module_settings.dart';

import 'interface_status_test.dart' show InterfaceRadio;
import 'module_diagnostics_test.dart' show FieldRadio;

class GroupRadio extends InterfaceRadio {
  GroupRadio() {
    groupSettings = true;
  }
  bool holdAck = false;
  bool rejectGroup = false;
  bool wrongReadback = false;
  bool loseReadback = false;

  @override
  Future<void> write(Uint8List bytes) async {
    final frame = UsbLineDecoder().add(bytes).single;
    final query = frame.command == 0x10 ? decodeNfcp(frame.payload) : null;
    if (query?.command != 0x31) return super.write(bytes);
    queryCommands.add(0x31);
    stream.add(usbLine(BridgeFrame(frame.sequence, 0x10, const [0], flags: 2)));
    if (holdAck) return;
    final uid = query!.target!;
    if (rejectGroup) {
      reply(uid, 0x51, [0x31, 1]);
      return;
    }
    if (!wrongReadback) groups[uid] = readUnsigned(query.data, 0, 2);
    if (loseReadback) badNonce = true;
    // Neither a different source nor an ACK for a different command may satisfy this write.
    reply(uid + 1, 0x50, [0x31, 0]);
    reply(uid, 0x50, [0x52, 0]);
    reply(uid, 0x50, [0x31, 0]);
  }
}

Future<RadioSession> connected(GroupRadio radio) async {
  final session = RadioSession(
    radio,
    discoveryRounds: 1,
    discoveryInterval: Duration.zero,
    moduleTimeout: const Duration(milliseconds: 40),
  );
  await session.connect('field');
  await session.discoverModules();
  return session;
}

void main() {
  test('Module script status follows its reported ID, including zero, rather than event count', () {
    final module = FieldModule(1, 1, 24, 1);
    for (final status in ['', 'id=-- entries=10', 'id=invalid entries=10']) {
      module.info['Script'] = status;
      expect(module.scriptId, isNull);
      expect(module.scriptStatus, 'Not loaded');
    }
    for (final (status, id) in [
      ('id=0000 entries=0', 0),
      ('id=00AF entries=12', 175),
      ('ID=ffff run=0', 65535),
    ]) {
      module.info['Script'] = status;
      expect(module.scriptId, id);
      expect(module.scriptStatus, 'Loaded');
    }
  });
  test(
    'Group setting matches Python golden packet and validates numeric limits',
    () {
      final query = decodeNfcp(moduleGroupSetting(0x12ff5678, 65535))!;
      expect(
        moduleGroupSetting(
          0x12ff5678,
          65535,
        ).map((byte) => byte.toRadixString(16).padLeft(2, '0')).join(),
        'fffb080112fffe567831fffefffe85e2',
      );
      expect(query.target, 0x12ff5678);
      expect(query.command, 0x31);
      expect(query.data, [255, 255]);
      expect(() => moduleGroupSetting(1, 65536), throwsArgumentError);
      expect(() => moduleGroupSetting(1, -1), throwsArgumentError);
    },
  );
  test('Group changes only after ACK and discovery readback; snapshots and script mapping follow', () async {
    final radio = GroupRadio();
    final session = await connected(radio);
    await session.readModule(FieldRadio.uid, resistance: true);
    final module = session.modules[FieldRadio.uid]!;
    final snapshot = module.resistance;
    expect(await session.setModuleGroup(module.uid, 2), isTrue);
    expect(module.group, 2);
    expect(module.resistance, same(snapshot));
    expect(module.info['Battery mV'], '12000');
    final script = ScriptDocument.parse(
      'show.csv',
      '#Event Time,#Channel,#Cue,#Description\n0,2,1,Test\n',
    );
    expect(
      ScriptReport.check(
        script,
        session.modules.values,
        radioReady: true,
      ).errors,
      0,
    );
    expect(session.status, contains('saved and confirmed'));
    session.dispose();
  });
  test(
    'Duplicate and invalid groups are rejected before transmitting',
    () async {
      final radio = GroupRadio();
      final session = await connected(radio);
      session.modules[99] = FieldModule(99, 2, 4, 1);
      await expectLater(
        session.setModuleGroup(FieldRadio.uid, 2),
        throwsArgumentError,
      );
      await expectLater(
        session.setModuleGroup(FieldRadio.uid, 65536),
        throwsArgumentError,
      );
      expect(radio.queryCommands.where((command) => command == 0x31), isEmpty);
      session.dispose();
    },
  );
  test('Missing ACK and missing readback never report a saved group', () async {
    for (final loseReadback in [false, true]) {
      final radio = GroupRadio()
        ..holdAck = !loseReadback
        ..loseReadback = loseReadback;
      final session = await connected(radio);
      expect(await session.setModuleGroup(FieldRadio.uid, 2), isFalse);
      expect(session.modules[FieldRadio.uid]!.group, 1);
      expect(session.modules[FieldRadio.uid]!.seenInScan, isFalse);
      expect(session.status, contains('unconfirmed'));
      session.dispose();
    }
  });
  test('Armed rejection and mismatched readback cannot pass', () async {
    for (final armed in [false, true]) {
      final radio = GroupRadio()
        ..rejectGroup = armed
        ..wrongReadback = !armed;
      final session = await connected(radio);
      expect(await session.setModuleGroup(FieldRadio.uid, 2), isFalse);
      expect(session.modules[FieldRadio.uid]!.group, 1);
      expect(session.status, contains(armed ? 'armed' : 'expected 2'));
      session.dispose();
    }
  });
  test(
    'Disconnect cancels a group change and old firmware cannot write groups',
    () async {
      final radio = GroupRadio()..holdAck = true;
      final session = await connected(radio);
      final operation = session.setModuleGroup(FieldRadio.uid, 2);
      await Future<void>.delayed(Duration.zero);
      await session.disconnect();
      expect(await operation, isFalse);
      expect(session.status, 'Disconnected');
      session.dispose();
      final old = GroupRadio()..groupSettings = false;
      final legacy = await connected(old);
      expect(await legacy.setModuleGroup(FieldRadio.uid, 2), isFalse);
      expect(old.queryCommands.contains(0x31), isFalse);
      legacy.dispose();
    },
  );
  test(
    'Battery icon uses 6.4–8.4 V with clamped estimates and unknown state',
    () {
      for (final (voltage, percent) in [
        (6000, 0),
        (6400, 0),
        (7400, 50),
        (8400, 100),
        (9000, 100),
      ]) {
        expect(ModuleBattery(millivolts: voltage).percent, percent);
      }
      expect(const ModuleBattery(millivolts: null).percent, isNull);
      expect(const ModuleBattery(millivolts: 0).percent, isNull);
    },
  );
  testWidgets('Module settings validates and saves a confirmed group', (
    tester,
  ) async {
    final radio = GroupRadio();
    late RadioSession session;
    await tester.runAsync(() async {
      session = await connected(radio);
    });
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: AnimatedBuilder(
              animation: session,
              builder: (context, _) => ModuleSettings(
                module: session.modules[FieldRadio.uid]!,
                session: session,
              ),
            ),
          ),
        ),
      ),
    );
    await tester.enterText(find.byType(TextFormField), '65536');
    await tester.pump();
    await tester.tap(find.text('Save group'));
    await tester.pumpAndSettle();
    expect(find.text('Enter a group from 0 to 65535.'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField), '2');
    await tester.pump();
    await tester.tap(find.text('Save group'));
    await tester.pumpAndSettle();
    expect(find.text('Group 2 saved and confirmed.'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
    session.dispose();
  });
}
