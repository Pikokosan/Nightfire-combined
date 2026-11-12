import 'dart:async';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'auto_continuity_test.dart' show HeartbeatRadio;
import 'module_diagnostics_test.dart' show FieldRadio;

/// A bridge with the pairing command (capability 4). Modules in [pairing]
/// answer the offer like field-module 0.3.1.
class PairingRadio extends HeartbeatRadio {
  PairingRadio({this.capabilities = 4 | 9});
  final int capabilities;
  final pairing = <int>[];
  final windows = <int>[];
  int network = 0x04d2;

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    List<int>? payload;
    if (request.command == 0x17) {
      payload = [1, capabilities, 1, 0, 1, 0, 0, 0, 11, 184, 6, 255, 4, 0, 0];
    } else if (request.command == 0x1c && request.payload.length == 1) {
      if (capabilities & 4 == 0) return super.write(bytes);
      windows.add(request.payload[0]);
      payload = [request.payload[0]];
      Timer(const Duration(milliseconds: 50), () {
        for (final uid in pairing) {
          final echo = [...bigEndian(network, 2), 90, 0x12, 0x34, 0, 3];
          reply(uid, 0x35, echo);
          reply(uid, 0x35, echo); // repeats
        }
        // A stray reply for another network is ignored.
        reply(0x0badf00d, 0x35, [0x77, 0x77, 90, 0x12, 0x34, 0, 1]);
      });
    }
    if (payload == null) return super.write(bytes);
    stream.add(
      usbLine(
        BridgeFrame(request.sequence, request.command, payload, flags: 2),
      ),
    );
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  Future<RadioSession> connected(PairingRadio radio) async {
    final session = RadioSession(
      radio,
      discoveryRounds: 1,
      discoveryInterval: Duration.zero,
      moduleTimeout: const Duration(milliseconds: 200),
    );
    await session.connect('field');
    for (var i = 0; i < 40 && session.interfaceStatus == null; i++) {
      await Future<void>.delayed(const Duration(milliseconds: 50));
    }
    return session;
  }

  test('pairs modules in pairing mode, then discovers them', () async {
    final radio = PairingRadio()..pairing.add(FieldRadio.uid);
    final session = await connected(radio);
    addTearDown(session.dispose);
    expect(session.supportsPairing, isTrue);
    final paired = await session.pairModules(seconds: 1);
    expect(radio.windows, [1]);
    expect(paired, {FieldRadio.uid});
    expect(session.pairing, isFalse);
    expect(session.modules[FieldRadio.uid]?.seenInScan, isTrue);
    expect(session.status, startsWith('Paired 1 module'));
  });

  test('reports when no module was in pairing mode', () async {
    final radio = PairingRadio();
    final session = await connected(radio);
    addTearDown(session.dispose);
    expect(await session.pairModules(seconds: 1), isEmpty);
    expect(session.status, contains('hold UP'));
  });

  test('refused while armed and on older bridges', () async {
    final radio = PairingRadio();
    final session = await connected(radio);
    addTearDown(session.dispose);
    session.master.armed.add(FieldRadio.uid);
    expect(session.canPair, isFalse);
    expect(() => session.pairModules(seconds: 1), throwsStateError);

    final old = PairingRadio(capabilities: 9);
    final oldSession = await connected(old);
    addTearDown(oldSession.dispose);
    expect(oldSession.supportsPairing, isFalse);
    expect(
      () => oldSession.pairModules(seconds: 1),
      throwsA(
        isA<StateError>().having(
          (e) => e.message,
          'message',
          contains('usb-master-0.4.5'),
        ),
      ),
    );
    expect(old.windows, isEmpty);
  });

  test('offer and reply frames decode as module packets', () {
    final frame = encodeNfcp(0xfa, [
      ...bigEndian(FieldRadio.uid, 4),
      0x35,
      4,
      210,
      90,
      0,
      1,
      0,
      3,
    ]);
    final packet = decodeNfcp(frame)!;
    expect(packet.command, 0x35);
    expect(packet.source, FieldRadio.uid);
  });
}
