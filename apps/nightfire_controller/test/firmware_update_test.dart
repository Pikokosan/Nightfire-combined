import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/transport/radio_transport.dart';
import 'package:nightfire_controller/widgets/firmware_panel.dart';

/// A USB bridge plus one field module that implements module_ota.py and the
/// bridge's file commands, with switches for lost replies.
class FirmwareRadio implements RadioTransport {
  final stream = StreamController<Uint8List>.broadcast();
  final decoder = UsbLineDecoder();
  final bridge = <BridgeFrame>[];
  final packets = <NfcpPacket>[];

  String bridgeFirmware = 'usb-master-0.4.0';
  final bridgeFiles = <String, List<int>>{};
  String? _bridgeName;
  List<int> _bridgeData = [];
  int dropBridgeWriteReplies = 0;
  bool reconnectFails = false;

  String moduleFirmware = '0.3.0';
  final moduleFiles = <String, List<int>>{};
  bool moduleArmed = false;
  bool _otaActive = false;
  int _transfer = 0;
  String _path = '';
  int _size = 0;
  int _checksum = 0;
  List<int> _received = [];
  /// Commands whose next reply is lost after the module acted on them.
  final dropReplies = <int, int>{};
  bool comesBackAsDummy = false;
  /// The chunk at this offset is lost over RF, but a late duplicate ACK for
  /// the previous chunk arrives in its place.
  int? loseChunkWithStaleAck;

  @override
  Stream<Uint8List> get incoming => stream.stream;
  @override
  Future<List<RadioDevice>> devices() async => [];
  @override
  Future<void> connect(String id) async {
    if (reconnectFails) throw StateError('USB device not found');
  }

  @override
  Future<void> disconnect() async {}
  @override
  Future<void> dispose() => stream.close();

  void _bridgeReply(BridgeFrame request, List<int> payload, {int flags = 2}) {
    stream.add(
      usbLine(
        BridgeFrame(
          request.sequence,
          request.command == 1 ? 2 : request.command,
          payload,
          flags: flags,
        ),
      ),
    );
  }

  void _moduleReply(int uid, int command, List<int> data) => stream.add(
    usbLine(
      BridgeFrame(0, 0x11, [
        0,
        0,
        0,
        ...encodeNfcp(0xfa, [...bigEndian(uid, 4), command, ...data]),
      ]),
    ),
  );

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    bridge.add(request);
    final payload = request.payload;
    switch (request.command) {
      case 1:
        _bridgeReply(request, []);
      case 3:
        final version = utf8.encode(bridgeFirmware);
        _bridgeReply(request, [
          1, 2, 3, 4, 0, 0, 4, 0xd2, version.length, ...version, //
        ]);
      case 0x15:
        _bridgeReply(request, List.filled(34, 0));
      case 0x17:
        _bridgeReply(request, [1, 8, 0, 0, 1, 0, 0, 0, 11, 184, 255, 255, 4, 0, 0]);
      case 0x32:
        _bridgeName = utf8.decode(payload.sublist(1, 1 + payload[0]));
        _bridgeData = [];
        _bridgeReply(request, []);
      case 0x33:
        final offset = readUnsigned(payload, 0, 4);
        if (offset == _bridgeData.length) _bridgeData.addAll(payload.skip(4));
        if (dropBridgeWriteReplies > 0) {
          dropBridgeWriteReplies--;
          return;
        }
        _bridgeReply(request, []);
      case 0x34:
        bridgeFiles[_bridgeName!] = _bridgeData;
        _bridgeReply(request, []);
      case 0x31:
        _bridgeReply(request, []);
        final bundle = bridgeFiles['usb_master_bundle.mpy'];
        if (bundle != null) {
          bridgeFirmware = embeddedBridgeVersion(bundle) ?? bridgeFirmware;
        }
      case 0x10:
        _bridgeReply(request, [0]);
        _field(decodeNfcp(payload)!);
      default:
        _bridgeReply(request, []);
    }
  }

  void _field(NfcpPacket packet) {
    packets.add(packet);
    final uid = packet.target;
    if (uid == null) return;
    final data = packet.data;
    void ack() {
      final dropped = dropReplies[packet.command] ?? 0;
      if (dropped > 0) {
        dropReplies[packet.command] = dropped - 1;
        return;
      }
      _moduleReply(uid, 0x50, [packet.command, 0]);
    }

    void nack(int code) => _moduleReply(uid, 0x51, [packet.command, code]);
    switch (packet.command) {
      case 0x60:
        if (moduleArmed) return nack(1);
        _otaActive = true;
        _transfer = readUnsigned(data, 0, 2);
        _size = readUnsigned(data, 2, 4);
        _checksum = readUnsigned(data, 6, 4);
        _path = utf8.decode(data.sublist(11));
        _received = [];
        ack();
      case 0x61:
        final offset = readUnsigned(data, 2, 4);
        final chunk = data.sublist(6);
        if (!_otaActive || readUnsigned(data, 0, 2) != _transfer) {
          return nack(3);
        }
        if (offset == loseChunkWithStaleAck) {
          loseChunkWithStaleAck = null;
          return _moduleReply(uid, 0x50, [0x61, 0]);
        }
        if (offset < _received.length &&
            offset + chunk.length <= _received.length) {
          return ack(); // Duplicate after a lost ACK.
        }
        if (offset != _received.length) return nack(3);
        _received.addAll(chunk);
        ack();
      case 0x62:
        final sum = _received.fold<int>(0, (s, b) => (s + b) & 0xffffffff);
        if (!_otaActive || _received.length != _size || sum != _checksum) {
          _otaActive = false;
          return nack(4);
        }
        moduleFiles[_path] = _received;
        _otaActive = false;
        ack();
      case 0x63:
        _otaActive = false;
        ack();
      case 0x64:
        final image = moduleFiles['field_main.mpy'];
        if (image != null) moduleFirmware = utf8.decode(image).trim();
        ack();
      case 0x52:
        final fields = [
          comesBackAsDummy ? 'dummy' : 'field-module',
          moduleFirmware,
        ];
        _moduleReply(uid, 0x53, [
          for (final field in fields) ...[
            utf8.encode(field).length,
            ...utf8.encode(field),
          ],
        ]);
      case 0x27:
        _moduleReply(uid, 0x28, [
          1,
          moduleArmed ? 0x09 : 0,
          24,
          ...List.filled(16, 0),
          0, 0, 0, 0, 0, 0xff, 0xff, 0xff, //
        ]);
      default:
        ack();
    }
  }
}

void main() {
  const uid = 0x12ff5678;
  late FirmwareRadio radio;
  late RadioSession session;

  setUp(() async {
    radio = FirmwareRadio();
    session = RadioSession(
      radio,
      moduleTimeout: const Duration(milliseconds: 40),
    );
    session.master
      ..updateAckTimeout = const Duration(milliseconds: 40)
      ..rebootDelay = Duration.zero
      ..updateSettle = Duration.zero
      ..reconnectDelay = const Duration(milliseconds: 10);
    await session.connect('test-radio');
    session.modules[uid] = FieldModule(uid, 3, 24, 0x20)
      ..seenInScan = true
      ..info = {'Firmware': '0.3.0'};
  });
  tearDown(() => session.dispose());

  List<int> image(String version, int size) =>
      [...utf8.encode(version)] + List.filled(size, 0x20);

  test('field update retries lost replies, restarts and checks version', () async {
    final firmware = utf8.encode('0.3.1');
    radio.dropReplies[0x61] = 2; // Two chunk ACKs lost over RF.
    radio.dropReplies[0x64] = 1; // Module reset beats its reboot ACK.
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        firmware,
      ),
      isTrue,
      reason: session.master.status,
    );
    expect(radio.moduleFiles['field_main.mpy'], firmware);
    expect(session.master.firmwareResults[uid], 'Updated • 0.3.0 → 0.3.1');
    expect(session.modules[uid]!.info['Firmware'], '0.3.1');
    expect(session.modules[uid]!.seenInScan, isTrue);
    expect(session.master.updateProgress, isNull);
  });

  test('large images go in 80-byte chunks with exact offsets', () async {
    final firmware = image('0.3.1', 200);
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        firmware,
      ),
      isTrue,
      reason: session.master.status,
    );
    final offsets = radio.packets
        .where((p) => p.command == 0x61)
        .map((p) => readUnsigned(p.data, 2, 4));
    expect(offsets, [0, 80, 160]);
    expect(radio.moduleFiles['field_main.mpy'], firmware);
  });

  test('a stale ACK that skips a chunk is realigned, not fatal', () async {
    final firmware = image('0.3.1', 600);
    radio.loseChunkWithStaleAck = 320;
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        firmware,
      ),
      isTrue,
      reason: session.master.status,
    );
    expect(radio.moduleFiles['field_main.mpy'], firmware);
    final rejected = radio.packets.where((p) => p.command == 0x61).length;
    expect(rejected, greaterThan((firmware.length / 80).ceil()));
  });

  test('a lost END ACK still counts once the module restarts', () async {
    radio.dropReplies[0x62] = 1;
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        utf8.encode('0.3.1'),
      ),
      isTrue,
      reason: session.master.status,
    );
    expect(session.master.firmwareResults[uid], 'Updated • 0.3.0 → 0.3.1');
  });

  test('an armed module is refused and never restarted', () async {
    radio.moduleArmed = true;
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        utf8.encode('0.3.1'),
      ),
      isFalse,
    );
    expect(radio.packets.map((p) => p.command), isNot(contains(0x64)));
    expect(session.master.firmwareResults[uid], startsWith('Failed'));
  });

  test('a module that comes back with other firmware is reported', () async {
    radio.comesBackAsDummy = true;
    expect(
      await session.master.updateModuleFirmware(
        [uid],
        'field_main.mpy',
        utf8.encode('0.3.1'),
      ),
      isFalse,
    );
    expect(session.master.firmwareResults[uid], contains('"dummy"'));
  });

  test('cancel aborts the transfer and leaves the module file alone', () async {
    radio.dropReplies[0x61] = 3; // Slow link: cancel lands mid-transfer.
    final update = session.master.updateModuleFirmware(
      [uid],
      'field_main.mpy',
      image('0.3.1', 4000),
    );
    await Future<void>.delayed(const Duration(milliseconds: 20));
    session.master.cancelUpdate();
    expect(await update, isFalse);
    expect(radio.packets.last.command, 0x63);
    expect(radio.moduleFiles, isEmpty);
    expect(session.master.firmwareResults[uid], 'Failed: Bad state: Update cancelled');
  });

  test('bad module paths are refused before anything is sent', () async {
    final sent = radio.packets.length;
    expect(
      await session.master.updateModuleFirmware([uid], '../main.py', [1]),
      isFalse,
    );
    expect(radio.packets, hasLength(sent));
  });

  test('radio update sends the bundle first, restarts and reconnects', () async {
    final bundle = image('usb-master-0.4.1', 100);
    final entry = utf8.encode('import usb_master_bundle\n');
    radio.dropBridgeWriteReplies = 1; // Lost USB reply: chunk is re-sent.
    expect(
      await session.master.updateBridgeFirmware({
        'main.py': entry,
        'usb_master_bundle.mpy': bundle,
      }),
      isTrue,
      reason: session.master.bridgeUpdateResult,
    );
    final begins = radio.bridge
        .where((f) => f.command == 0x32)
        .map((f) => utf8.decode(f.payload.sublist(1, 1 + f.payload[0])));
    expect(begins, ['usb_master_bundle.mpy', 'main.py']);
    expect(radio.bridgeFiles['usb_master_bundle.mpy'], bundle);
    expect(radio.bridgeFiles['main.py'], entry);
    expect(radio.bridge.map((f) => f.command), contains(0x31));
    expect(session.ready, isTrue);
    expect(session.values['Firmware'], 'usb-master-0.4.1');
    expect(session.master.supported, isTrue);
    expect(session.master.bridgeUpdateResult, 'Radio updated • running usb-master-0.4.1');
  }, timeout: const Timeout(Duration(seconds: 20)));

  test('radio config and unknown files are refused', () async {
    expect(
      await session.master.updateBridgeFirmware({
        'nrf_bridge_config.py': [1],
      }),
      isFalse,
    );
    expect(radio.bridge.where((f) => f.command == 0x32), isEmpty);
    expect(session.master.bridgeUpdateResult, contains('nrf_bridge_config.py'));
  });

  test('a radio that does not come back is reported', () async {
    radio.reconnectFails = true;
    expect(
      await session.master.updateBridgeFirmware({
        'usb_master_bundle.mpy': image('usb-master-0.4.1', 10),
      }),
      isFalse,
    );
    expect(session.master.bridgeUpdateResult, contains('did not reconnect'));
  }, timeout: const Timeout(Duration(seconds: 20)));

  test('Android picker .bin copies get their .mpy name back', () {
    expect(restorePickedName('usb_master_bundle.bin'), 'usb_master_bundle.mpy');
    expect(restorePickedName('field_main.BIN'), 'field_main.mpy');
    expect(restorePickedName('main.py'), 'main.py');
    expect(restorePickedName('field_main.mpy'), 'field_main.mpy');
    expect(restorePickedName('.bin'), '.bin');
  });

  test('embedded bridge version is read from the compiled bundle', () {
    expect(
      embeddedBridgeVersion([0, 1, ...utf8.encode('xusb-master-0.4.12\x00')]),
      'usb-master-0.4.12',
    );
    expect(embeddedBridgeVersion([1, 2, 3]), isNull);
    expect(isMasterFirmware('usb-master-0.4.1'), isTrue);
    expect(isMasterFirmware('usb-master-0.5.0'), isFalse);
    expect(isMasterFirmware('usb-monitor-0.3.2'), isFalse);
  });

  testWidgets('Firmware page picks files and updates selected modules', (
    tester,
  ) async {
    await tester.runAsync(() async {
      Future<List<PickedFile>> pick({bool multiple = false}) async => [
        (name: 'field_main.mpy', bytes: utf8.encode('0.3.1')),
      ];
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: ListenableBuilder(
                listenable: session,
                builder: (context, _) =>
                    FirmwarePanel(session: session, pick: pick),
              ),
            ),
          ),
        ),
      );
      expect(find.text('Installed: usb-master-0.4.0'), findsOneWidget);
      await tester.tap(find.text('Choose module firmware'));
      await tester.pumpAndSettle();
      expect(find.text('field_main.mpy (5 bytes)'), findsOneWidget);
      await tester.tap(find.text('Select all'));
      await tester.pump();
      await tester.tap(find.text('Update 1 module(s)'));
      await tester.pump();
      for (var i = 0; i < 50 && session.busy; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 20));
      }
      await tester.pump();
      expect(find.textContaining('Updated • 0.3.0 → 0.3.1'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    });
  });

  testWidgets('Firmware page rejects files that are not radio firmware', (
    tester,
  ) async {
    Future<List<PickedFile>> pick({bool multiple = false}) async => [
      (name: 'notes.txt', bytes: [1]),
    ];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: FirmwarePanel(session: session, pick: pick),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Choose radio files'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Not radio firmware: notes.txt'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });
}
