import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/transport/radio_transport.dart';

/// A mesh bridge: answers 0x1A with the module's reply (as a normal 0x11
/// frame carrying a hop count) and then the delivery result.
class MeshBridge implements RadioTransport {
  final stream = StreamController<Uint8List>.broadcast();
  final decoder = UsbLineDecoder();
  final requests = <BridgeFrame>[];
  bool delivered = true;
  int hops = 1;
  int route = 1; // via
  int relay = 0x5A000112;

  @override
  Stream<Uint8List> get incoming => stream.stream;
  @override
  Future<List<RadioDevice>> devices() async => [];
  @override
  Future<void> connect(String id) async {}
  @override
  Future<void> disconnect() async {}
  @override
  Future<void> dispose() => stream.close();

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    requests.add(request);
    if (request.command != 0x1a) {
      stream.add(usbLine(BridgeFrame(request.sequence, request.command, [0], flags: 2)));
      return;
    }
    final uid = readUnsigned(request.payload, 0, 4);
    final packet = decodeNfcp(request.payload.sublist(6))!;
    if (delivered && packet.command == 0x52) {
      final text = utf8.encode('id=-- entries=0 run=0');
      stream.add(usbLine(BridgeFrame(0, 0x11, [
        0xff, 0xd3, hops, // rssi -45, hops
        ...encodeNfcp(0xfa, [...bigEndian(uid, 4), 0x53, 0, 0, 0, text.length, ...text]),
      ])));
    }
    stream.add(usbLine(BridgeFrame(request.sequence, 0x1a, [
      delivered ? 0 : 1, delivered ? route : 2, ...bigEndian(delivered ? relay : 0, 4), delivered ? 1 : 3,
    ], flags: 2)));
  }
}

void main() {
  test('reliable delivery is used only by mesh bridges', () {
    expect(supportsReliableDelivery('usb-master-0.4.1'), isFalse);
    expect(supportsReliableDelivery('usb-master-0.4.2'), isTrue);
    expect(supportsReliableDelivery('usb-master-0.5.0'), isTrue);
    expect(supportsReliableDelivery('usb-monitor-0.3.2'), isFalse);
    expect(supportsReliableDelivery('usb-monitor-0.3.3'), isTrue);
    expect(supportsReliableDelivery('Unknown'), isFalse);
    expect(supportsReliableDelivery(null), isFalse);
    // The mesh master is still a master build for everything else.
    expect(isMasterFirmware('usb-master-0.4.2'), isTrue);
  });

  test('payload and result encoding', () {
    expect(
      reliablePayload(0x01020304, const Duration(milliseconds: 4000), [0xff, 0xfb]),
      [1, 2, 3, 4, 0x0f, 0xa0, 0xff, 0xfb],
    );
    final delivery = Delivery.parse([0, 1, 0x5a, 0, 1, 0x12, 2])!;
    expect(delivery.delivered, isTrue);
    expect(delivery.route, ModuleRoute.relayed);
    expect(delivery.relay, 0x5A000112);
    expect(delivery.attempts, 2);
    expect(Delivery.parse([0, 9, 0, 0, 0, 0, 1]), isNull);
  });

  group('session', () {
    const uid = 0x12ff5678;
    late MeshBridge bridge;
    late RadioSession session;
    setUp(() {
      bridge = MeshBridge();
      session = RadioSession(bridge, moduleTimeout: const Duration(milliseconds: 300));
      session.ready = true;
      session.values['Firmware'] = 'usb-master-0.4.2';
      session.modules[uid] = FieldModule(uid, 1, 24, 0x20)..seenInScan = true;
    });
    tearDown(() => session.dispose());

    test('a query goes out as 0x1A and records the route and hops', () async {
      await session.readModule(uid);
      final request = bridge.requests.single;
      expect(request.command, 0x1a);
      expect(readUnsigned(request.payload, 0, 4), uid);
      expect(readUnsigned(request.payload, 4, 2), 300);
      expect(decodeNfcp(request.payload.sublist(6))!.command, 0x52);
      final module = session.modules[uid]!;
      expect(module.error, isNull);
      expect(module.hops, 1);
      expect(module.route, ModuleRoute.relayed);
      expect(module.routeText, 'Via 0x5A000112');
    });

    test('a failed delivery fails the query quickly', () async {
      bridge.delivered = false;
      final started = DateTime.now();
      await session.readModule(uid);
      expect(session.modules[uid]!.error, contains('3 attempts'));
      expect(DateTime.now().difference(started), lessThan(const Duration(seconds: 1)));
    });

    test('older bridges keep plain SEND_NFCP', () async {
      session.values['Firmware'] = 'usb-master-0.4.1';
      await session.readModule(uid);
      expect(bridge.requests.single.command, 0x10);
    });
  });
}
