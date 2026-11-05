import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/transport/radio_transport.dart';

class FieldRadio implements RadioTransport {
  final stream = StreamController<Uint8List>.broadcast();
  final decoder = UsbLineDecoder();
  final queryCommands = <int>[];
  final measurementTargets = <int>[];
  List<int> moduleUids = [uid];
  final silentUids = <int>{};
  final groups = <int, int>{};
  bool respond = true;
  bool badNonce = false;
  bool rejectMeasurement = false;
  bool holdMeasurement = false;
  bool legacyOnly = false;
  static const uid = 0x12ff5678;

  @override
  Stream<Uint8List> get incoming => stream.stream;
  @override
  Future<List<RadioDevice>> devices() async => [
    const RadioDevice('field', 'Radio'),
  ];
  @override
  Future<void> connect(String id) async {}
  @override
  Future<void> disconnect() async {}
  @override
  Future<void> dispose() => stream.close();

  void reply(int source, int command, List<int> data) {
    final frame = encodeNfcp(0xfa, [...bigEndian(source, 4), command, ...data]);
    final line = usbLine(BridgeFrame(0, 0x11, [255, 191, 0, ...frame]));
    stream.add(Uint8List.sublistView(line, 0, 9));
    stream.add(Uint8List.sublistView(line, 9));
  }

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    var payload = <int>[];
    if (request.command == 3) {
      final version = ascii.encode('usb-monitor-0.2');
      payload = [1, 2, 3, 4, 0, 0, 4, 210, version.length, ...version];
    } else if (request.command == 0x15) {
      payload = List.filled(34, 0);
    } else if (request.command == 0x10) {
      final query = decodeNfcp(request.payload)!;
      final uid = query.target ?? FieldRadio.uid;
      queryCommands.add(query.command);
      if (query.command == 0x24) measurementTargets.add(uid);
      payload = [0];
      if (respond) {
        if (query.command == 0x40) {
          final nonce = badNonce ? [255, 255] : query.data;
          for (final uid in moduleUids) {
            final data = [
              ...bigEndian(groups[uid] ?? 1, 2),
              ...bigEndian(uid, 4),
              4,
              1,
              0x20,
              0,
              ...nonce,
            ];
            reply(uid, 0x41, data);
            reply(uid, 0x41, data);
          }
        } else if (query.command == 0x52) {
          final info = [
            'nrf-field',
            'test',
            'native',
            '',
            '',
            '',
            '12000',
            'LiPo',
          ];
          reply(uid + 1, 0x53, [0]);
          reply(uid, 0x53, [
            for (final text in info) ...[
              utf8.encode(text).length,
              ...utf8.encode(text),
            ],
          ]);
        } else if (query.command == 0x24 &&
            !holdMeasurement &&
            !legacyOnly &&
            !silentUids.contains(uid)) {
          if (rejectMeasurement) {
            reply(uid, 0x51, [0x24, 4]);
          } else {
            reply(uid, 0x25, [1, 1, 4, 0, 0, 25, 0, 0, 255, 255, 255, 254]);
          }
        } else if (query.command == 0x22 && !silentUids.contains(uid)) {
          reply(uid, 0x23, [5]);
        }
      }
    }
    stream.add(
      usbLine(
        BridgeFrame(
          request.sequence,
          request.command == 1 ? 2 : request.command,
          payload,
          flags: 2,
        ),
      ),
    );
  }
}

RadioSession sessionFor(FieldRadio radio) =>
    RadioSession(radio, discoveryRounds: 1, discoveryInterval: Duration.zero);

void main() {
  test(
    'Batch measures every discovered module and continues after a timeout',
    () async {
      final radio = FieldRadio()
        ..moduleUids = [FieldRadio.uid + 2, FieldRadio.uid, FieldRadio.uid + 1];
      final session = RadioSession(
        radio,
        discoveryRounds: 1,
        discoveryInterval: Duration.zero,
        moduleTimeout: const Duration(milliseconds: 30),
      );
      await session.connect('field');
      await session.discoverModules();
      await session.readModule(FieldRadio.uid, resistance: true);
      radio.measurementTargets.clear();
      radio.silentUids.add(FieldRadio.uid);
      final progress = <String?>[];
      session.addListener(() {
        progress.add(session.continuityProgress);
      });
      final batch = session.checkAllContinuity();
      expect(session.busy, isTrue);
      await session.readModule(FieldRadio.uid + 1, resistance: true);
      await session.checkAllContinuity();
      await batch;
      expect(radio.measurementTargets, [
        FieldRadio.uid,
        FieldRadio.uid + 1,
        FieldRadio.uid + 2,
      ]);
      expect(session.modules[FieldRadio.uid]!.resistance, isNull);
      expect(session.modules[FieldRadio.uid]!.error, contains('No reply'));
      expect(session.modules[FieldRadio.uid + 1]!.resistance, isNotNull);
      expect(session.modules[FieldRadio.uid + 2]!.resistance, isNotNull);
      expect(progress, contains('Checking module 3 of 3…'));
      expect(session.status, 'Continuity check complete: 2 updated, 1 failed.');
      expect(session.busy, isFalse);
      expect(session.continuityProgress, isNull);
      session.dispose();
    },
  );

  test('Disconnect stops a batch before the next module', () async {
    final radio = FieldRadio()
      ..moduleUids = [FieldRadio.uid, FieldRadio.uid + 1];
    final session = sessionFor(radio);
    await session.connect('field');
    await session.discoverModules();
    radio.holdMeasurement = true;
    final batch = session.checkAllContinuity();
    await Future<void>.delayed(Duration.zero);
    await session.disconnect();
    await batch;
    expect(radio.measurementTargets, [FieldRadio.uid]);
    expect(session.status, 'Disconnected');
    expect(session.busy, isFalse);
    expect(session.continuityProgress, isNull);
    session.dispose();
  });
  test(
    'Older modules fall back to continuity after a resistance timeout',
    () async {
      final radio = FieldRadio()..legacyOnly = true;
      final session = RadioSession(
        radio,
        discoveryRounds: 1,
        discoveryInterval: Duration.zero,
        moduleTimeout: const Duration(milliseconds: 30),
      );
      await session.connect('field');
      await session.discoverModules();
      await session.readModule(FieldRadio.uid, resistance: true);
      final snapshot = session.modules[FieldRadio.uid]!.resistance!;
      expect(snapshot.continuityOnly, isTrue);
      expect(snapshot.cues.map((cue) => cue.label), [
        'Open',
        'Connected',
        'Open',
        'Connected',
      ]);
      expect(radio.queryCommands, [0x40, 0x52, 0x24, 0x22]);
      session.dispose();
    },
  );
  test('A silent module times out without inventing continuity', () async {
    final radio = FieldRadio();
    final session = RadioSession(
      radio,
      discoveryRounds: 1,
      discoveryInterval: Duration.zero,
      moduleTimeout: const Duration(milliseconds: 30),
    );
    await session.connect('field');
    await session.discoverModules();
    radio.respond = false;
    await session.readModule(FieldRadio.uid, resistance: true);
    expect(session.modules[FieldRadio.uid]!.resistance, isNull);
    expect(session.modules[FieldRadio.uid]!.error, contains('No reply'));
    expect(session.ready, isTrue);
    session.dispose();
  });
  test(
    'Discovery deduplicates replies, loads matching info and measures cues',
    () async {
      final radio = FieldRadio();
      final session = sessionFor(radio);
      await session.connect('field');
      await session.discoverModules();
      expect(session.modules.length, 1);
      final module = session.modules[FieldRadio.uid]!;
      expect(module.info['App'], 'nrf-field');
      expect(module.rssi, -65);
      await session.readModule(FieldRadio.uid, resistance: true);
      expect(module.resistance!.cues.map((cue) => cue.label), [
        'Connected',
        'Short',
        'Open',
        'Invalid',
      ]);
      expect(radio.queryCommands, [0x40, 0x52, 0x24]);
      session.dispose();
    },
  );
  test(
    'Wrong discovery nonce is ignored and missing modules are marked',
    () async {
      final radio = FieldRadio();
      final session = sessionFor(radio);
      await session.connect('field');
      await session.discoverModules();
      radio.badNonce = true;
      await session.discoverModules();
      expect(session.modules[FieldRadio.uid]!.seenInScan, isFalse);
      expect(session.status, contains('No field modules replied'));
      session.dispose();
    },
  );
  test('A rejected measurement never leaves an old good snapshot', () async {
    final radio = FieldRadio();
    final session = sessionFor(radio);
    await session.connect('field');
    await session.discoverModules();
    await session.readModule(FieldRadio.uid, resistance: true);
    radio.rejectMeasurement = true;
    await session.readModule(FieldRadio.uid, resistance: true);
    final module = session.modules[FieldRadio.uid]!;
    expect(module.resistance, isNull);
    expect(module.error, contains('rejected'));
    expect(session.ready, isTrue);
    session.dispose();
  });
  test(
    'Disconnect cancels a pending field query and clears the device list',
    () async {
      final radio = FieldRadio();
      final session = sessionFor(radio);
      await session.connect('field');
      await session.discoverModules();
      radio.holdMeasurement = true;
      final measurement = session.readModule(FieldRadio.uid, resistance: true);
      await Future<void>.delayed(Duration.zero);
      await session.disconnect();
      await measurement;
      expect(session.modules, isEmpty);
      expect(session.status, 'Disconnected');
      session.dispose();
    },
  );
}
