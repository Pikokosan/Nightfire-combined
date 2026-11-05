import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/transport/radio_transport.dart';

class FakeRadio implements RadioTransport {
  final controller = StreamController<Uint8List>.broadcast();
  final decoder = UsbLineDecoder();
  final commands = <int>[];
  bool failWrite = false;
  bool holdReplies = false;
  bool wrongSequenceFirst = false;
  bool closed = false;

  @override
  Stream<Uint8List> get incoming => controller.stream;
  @override
  Future<List<RadioDevice>> devices() async => [
    const RadioDevice('test', 'Test radio'),
  ];
  @override
  Future<void> connect(String id) async {
    closed = false;
  }

  @override
  Future<void> write(Uint8List bytes) async {
    if (failWrite) throw StateError('Write failed');
    final request = decoder.add(bytes).single;
    commands.add(request.command);
    if (holdReplies) return;
    var payload = <int>[];
    if (request.command == 3) payload = [0x52, 0x84, 0, 1, 0, 0, 4, 0xd2, 0];
    if (request.command == 0x15) {
      payload = List.filled(34, 0);
      payload[24] = 90;
    }
    final command = request.command == 1 ? 2 : request.command;
    if (wrongSequenceFirst) {
      controller.add(
        usbLine(
          BridgeFrame((request.sequence + 1) & 255, command, payload, flags: 2),
        ),
      );
    }
    controller.add(
      usbLine(BridgeFrame(request.sequence, command, payload, flags: 2)),
    );
  }

  @override
  Future<void> disconnect() async {
    closed = true;
  }

  @override
  Future<void> dispose() => controller.close();
}

void main() {
  test(
    'Handshake correlates replies and requests only status commands',
    () async {
      final radio = FakeRadio()..wrongSequenceFirst = true;
      final session = RadioSession(radio);
      await session.connect('test');
      expect(session.ready, isTrue);
      expect(session.values['Network'], '0x04D2');
      expect(session.values['Channel'], '90');
      expect(radio.commands, [1, 3, 0x15]);
      await session.refresh();
      expect(radio.commands.last, 0x15);
      session.dispose();
    },
  );

  test('Write failure closes the connection without a ready state', () async {
    final radio = FakeRadio()..failWrite = true;
    final session = RadioSession(radio);
    await session.connect('test');
    expect(session.ready, isFalse);
    expect(session.status, contains('Write failed'));
    expect(radio.closed, isTrue);
    session.dispose();
  });

  test('Disconnect cancels a pending handshake', () async {
    final radio = FakeRadio()..holdReplies = true;
    final session = RadioSession(radio);
    final connection = session.connect('test');
    await Future<void>.delayed(Duration.zero);
    await session.disconnect();
    await connection;
    expect(session.ready, isFalse);
    expect(session.values, isEmpty);
    expect(session.status, 'Disconnected');
    session.dispose();
  });

  test('Unplug invalidates connection readiness', () async {
    final radio = FakeRadio();
    final session = RadioSession(radio);
    await session.connect('test');
    radio.controller.addError(StateError('Cable removed'));
    await Future<void>.delayed(Duration.zero);
    expect(session.ready, isFalse);
    expect(session.status, contains('Cable removed'));
    session.dispose();
  });
}
