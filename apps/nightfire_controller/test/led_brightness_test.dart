import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/widgets/led_brightness_setting.dart';

import 'auto_continuity_test.dart' show HeartbeatRadio;

/// A usb-master-0.4.4 bridge: advertises capability 64 and answers 0x1B.
class BrightnessRadio extends HeartbeatRadio {
  BrightnessRadio({this.level = 12});
  int level;
  final sets = <int>[];

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    List<int>? payload;
    if (request.command == 0x17) {
      payload = [1, 64 | 9, switchPosition, 1, 1, faults, 0, 0, 11, 184, 6, 8, 4, 0, 0];
    } else if (request.command == 0x1B) {
      if (request.payload.length == 1) {
        level = request.payload[0];
        sets.add(level);
      }
      payload = [level];
    }
    if (payload == null) return super.write(bytes);
    stream.add(
      usbLine(BridgeFrame(request.sequence, request.command, payload, flags: 2)),
    );
  }
}

void main() {
  test('percent mapping keeps the default level and the dim end', () {
    expect(ledPercentFromLevel(12), 22);
    expect(ledLevelFromPercent(22), 12);
    expect(ledLevelFromPercent(100), 255);
    expect(ledLevelFromPercent(1), 1);
    expect(ledPercentFromLevel(255), 100);
    for (var level = 1; level <= 255; level++) {
      final percent = ledPercentFromLevel(level);
      expect(percent, inInclusiveRange(1, 100));
    }
  });

  group('session', () {
    late BrightnessRadio radio;
    late RadioSession session;

    setUp(() async {
      radio = BrightnessRadio(level: 40);
      session = RadioSession(radio);
      await session.connect('field');
    });
    tearDown(() => session.dispose());

    Future<void> untilRead() async {
      for (var i = 0; i < 60 && session.ledBrightness == null; i++) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
    }

    test('reads the brightness from the bridge and sets it', () async {
      await untilRead();
      expect(session.supportsLedBrightness, isTrue);
      expect(session.ledBrightness, 40);
      await session.setLedBrightness(120);
      expect(radio.sets, [120]);
      expect(session.ledBrightness, 120);
    });

    test('refuses while modules are armed', () async {
      await untilRead();
      session.master.armed.add(1);
      expect(() => session.setLedBrightness(50), throwsStateError);
      expect(radio.sets, isEmpty);
    });
  });

  test('an older bridge without the capability', () async {
    final radio = HeartbeatRadio();
    final session = RadioSession(radio);
    addTearDown(session.dispose);
    await session.connect('field');
    await Future<void>.delayed(const Duration(milliseconds: 600));
    expect(session.interfaceStatus, isNotNull);
    expect(session.supportsLedBrightness, isFalse);
    expect(session.ledBrightness, isNull);
    expect(() => session.setLedBrightness(50), throwsStateError);
  });
}
