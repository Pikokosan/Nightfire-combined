import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/protocol/master_codec.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/scripts/script_document.dart';
import 'package:nightfire_controller/transport/radio_transport.dart';
import 'package:nightfire_controller/widgets/manual_fire_panel.dart';
import 'package:nightfire_controller/widgets/script_runner_panel.dart';
import 'package:nightfire_controller/widgets/field_modules_panel.dart';
import 'package:nightfire_controller/widgets/maintenance_tools.dart';

import 'module_test_helpers.dart';

class MasterRadio implements RadioTransport {
  final stream = StreamController<Uint8List>.broadcast();
  final decoder = UsbLineDecoder();
  final packets = <NfcpPacket>[];
  final bridge = <BridgeFrame>[];
  int? hold, reject;
  bool wrongAck = false;
  // Lost RF replies: the module acts on the packet but its reply never arrives.
  int dropStatusReplies = 0;
  bool dropFireAck = false;
  int cueCount = 64;
  int dropDisarms = 0;
  int dropLoadReplies = 0;
  bool wrongReadback = false;
  final scriptIds = <int, int>{};
  final scriptCounts = <int, int>{};
  final armed = <int, bool>{};
  final scriptRunning = <int, bool>{};
  final fireSequences = <int, int>{};
  final lastFireMasks = <int, BigInt>{};
  Completer<void>? transmitGate;
  List<int> fixedMask(BigInt value) => List.generate(
    8,
    (index) => ((value >> ((7 - index) * 8)) & BigInt.from(255)).toInt(),
  );
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
  void reply(int uid, int command, List<int> data) {
    stream.add(
      usbLine(
        BridgeFrame(0, 0x11, [
          0,
          0,
          0,
          ...encodeNfcp(0xfa, [...bigEndian(uid, 4), command, ...data]),
        ]),
      ),
    );
  }

  @override
  Future<void> write(Uint8List bytes) async {
    final request = decoder.add(bytes).single;
    bridge.add(request);
    await transmitGate?.future;
    stream.add(
      usbLine(BridgeFrame(request.sequence, request.command, [0], flags: 2)),
    );
    if (request.command != 0x10) return;
    final packet = decodeNfcp(request.payload)!;
    packets.add(packet);
    // STOP halts the scheduler, broadcast or unique, and keeps the arm.
    if (packet.command == 0x73) {
      if (packet.target == null) {
        scriptRunning.updateAll((_, _) => false);
      } else {
        scriptRunning[packet.target!] = false;
      }
    }
    if (packet.command == 0x11 && packet.target == null) {
      armed.updateAll((_, _) => false);
      scriptRunning.updateAll((_, _) => false);
    }
    if (packet.target == null || packet.command == hold) return;
    if (packet.command == 0x11 && dropDisarms > 0) {
      dropDisarms--;
      return; // Unique DISARM lost over RF.
    }
    if (packet.command == reject) {
      reply(packet.target!, 0x51, [packet.command, 4]);
    } else if (packet.command == 0x7f) {
      reply(packet.target!, 0x7f, packet.data);
    } else if (packet.command == 0x52) {
      final id = (scriptIds[packet.target!] ?? 0)
          .toRadixString(16)
          .padLeft(4, '0');
      final text = utf8.encode(
        'id=$id entries=${wrongReadback ? 0 : scriptCounts[packet.target!] ?? 0} run=0',
      );
      reply(packet.target!, 0x53, [0, 0, 0, text.length, ...text]);
    } else if (packet.command == 0x27) {
      final target = packet.target!;
      if (dropStatusReplies > 0) {
        dropStatusReplies--;
        return;
      }
      final flags =
          (armed[target] == true ? 0x09 : 0) |
          (scriptRunning[target] == true ? 0x02 : 0);
      reply(target, 0x28, [
        1,
        flags,
        cueCount,
        ...List.filled(8, 0),
        ...fixedMask(lastFireMasks[target] ?? BigInt.zero),
        ...bigEndian(fireSequences[target] ?? 0, 4),
        0,
        fireSequences[target] == null ? 0xff : 0,
        ...bigEndian(scriptIds[target] ?? 0xffff, 2),
      ]);
    } else if (packet.command != 0x76) {
      if (packet.command == 0x70) {
        scriptIds[packet.target!] = readUnsigned(packet.data, 0, 2);
        scriptCounts[packet.target!] = 0;
      }
      if (packet.command == 0x71) {
        scriptCounts[packet.target!] =
            (scriptCounts[packet.target!] ?? 0) + packet.data[3];
      }
      if (packet.command == 0x10) armed[packet.target!] = true;
      if (packet.command == 0x11) {
        armed[packet.target!] = false;
        scriptRunning[packet.target!] = false;
      }
      if (packet.command == 0x20) {
        fireSequences[packet.target!] =
            (fireSequences[packet.target!] ?? 0) + 1;
        lastFireMasks[packet.target!] = packet.data.fold<BigInt>(
          BigInt.zero,
          (value, byte) => (value << 8) | BigInt.from(byte),
        );
      }
      if (packet.command == 0x72) scriptRunning[packet.target!] = true;
      if (packet.command == 0x20 && dropFireAck) return;
      if (packet.command == 0x71 && dropLoadReplies > 0) {
        dropLoadReplies--;
        return; // LOAD applied, its ACK lost over RF.
      }
      reply(packet.target! + (wrongAck ? 1 : 0), 0x50, [packet.command, 0]);
    } else {
      final target = packet.target!;
      fireSequences[target] = (fireSequences[target] ?? 0) + 1;
      final eventMs = readUnsigned(packet.data, 2, 4);
      // The standard fixture has one cue per stepped timestamp.
      lastFireMasks[target] = eventMs == 0 ? BigInt.one << 63 : BigInt.one;
    }
  }
}

void main() {
  const uid = 0x12ff5678;
  late MasterRadio radio;
  late RadioSession session;
  setUp(() {
    radio = MasterRadio();
    session = RadioSession(
      radio,
      moduleTimeout: const Duration(milliseconds: 20),
    );
    session.ready = true;
    session.values['Firmware'] = 'usb-master-0.4.0';
    session.modules[uid] = FieldModule(uid, 1, 64, 0x20)..seenInScan = true;
    session.master.updateSettle = const Duration(milliseconds: 1);
  });
  tearDown(() => session.dispose());

  ScriptDocument script([String rows = '0.000,1,64,first\n1.000,1,1,next']) =>
      ScriptDocument.parse(
        'show.csv',
        '#Event Time,Channel,Cue,Description\n$rows\nEND',
      );

  test(
    'wire frames match the original Python builders including all 64 bits',
    () {
      final golden = jsonDecode(
        File('test/fixtures/master_golden.json').readAsStringSync(),
      ) as Map<String, dynamic>;
      final all = (BigInt.one << 64) - BigInt.one;
      final frames = <String, List<int>>{
        'arm': masterUnique(uid, 0x10),
        'disarm': masterUnique(uid, 0x11),
        'fire64': masterUnique(uid, 0x20, maskBytes(BigInt.one << 63)),
        'fireAll': masterUnique(uid, 0x20, maskBytes(all)),
        'setUnique': masterUnique(uid, 0x30, bigEndian(0xff00ff00, 4)),
        'setNetwork': masterUnique(uid, 0x32, bigEndian(1234, 2)),
        'setChannel': masterUnique(uid, 0x33, [90]),
        'raw': masterUnique(uid, 0x24, [1]),
        'outputStatus': moduleQuery(uid, 0x27),
        'calibrate': masterUnique(uid, 0x26, [
          1,
          1,
          2,
          ...bigEndian(1234, 2),
          ...bigEndian(47, 2),
          255,
          255,
          0,
          0,
        ]),
        'clear': masterUnique(uid, 0x70, bigEndian(0xabcd, 2)),
        'load': scriptChunk(uid, 0xabcd, 2, [(1000, BigInt.one), (2000, all)]),
        'start': masterUnique(uid, 0x72, [
          0xab,
          0xcd,
          ...bigEndian(2000, 2),
          ...bigEndian(1000, 4),
          ...bigEndian(0x3fff0000, 4),
        ]),
        'stop': masterBroadcast(0x73, [0xab, 0xcd]),
        'sync': masterBroadcast(0x75, bigEndian(0x3fff0000, 4)),
        'heartbeat': masterBroadcast(0x74, [0xab, 0xcd, ...bigEndian(1000, 4)]),
        'step': masterUnique(uid, 0x76, [0xab, 0xcd, ...bigEndian(2000, 4)]),
        'otaBegin': otaBegin(uid, 0xabcd, 'main.py', List.filled(80, 255)),
        'otaWrite': masterUnique(uid, 0x61, [
          0xab,
          0xcd,
          0,
          0,
          0,
          0,
          ...List.filled(80, 255),
        ]),
        'otaEnd': masterUnique(uid, 0x62, [0xab, 0xcd]),
        'otaAbort': masterUnique(uid, 0x63, [0xab, 0xcd]),
        'otaReboot': masterUnique(uid, 0x64),
      };
      expect(frames.keys.toSet(), golden.keys.toSet());
      for (final entry in frames.entries) {
        expect(entry.value, golden[entry.key], reason: entry.key);
      }
    },
  );

  test('monitor firmware cannot send live commands', () async {
    session.values['Firmware'] = 'usb-monitor-0.3.2';
    expect(await session.master.armModule(uid), isFalse);
    expect(await session.master.uploadScript(script()), isFalse);
    expect(radio.packets, isEmpty);
  });

  test('fire requires matching successful arm ACK and a valid cue', () async {
    expect(await session.master.fire(uid, 64), isFalse);
    expect(radio.packets, isEmpty);
    expect(await session.master.armModule(uid), isTrue);
    expect(await session.master.fire(uid, 64), isTrue);
    expect(radio.packets.lastWhere((packet) => packet.command == 0x20).data, [
      128,
      0,
      0,
      0,
      0,
      0,
      0,
      0,
    ]);
    expect(await session.master.fire(uid, 65), isFalse);
    expect(await session.master.disarmModule(uid), isTrue);
    expect(session.master.armed, isEmpty);
  });

  test(
    'wrong source ACK never arms and FIRE is never automatically retried',
    () async {
      radio.wrongAck = true;
      expect(await session.master.armModule(uid), isFalse);
      expect(session.master.armed, isEmpty);
      radio.wrongAck = false;
      await session.master.armModule(uid);
      // The module never answers FIRE: the app does not wait or resend.
      radio.hold = 0x20;
      expect(await session.master.fire(uid, 1), isTrue);
      expect(radio.packets.where((p) => p.command == 0x20), hasLength(1));
      expect(session.master.armed, {uid});
      expect(session.master.lastFire, (uid: uid, cue: 1));
    },
  );

  test('manual fire sends one FIRE packet and reads nothing back', () async {
    await session.master.armModule(uid);
    final before = radio.packets.length;
    radio.dropFireAck = true;
    expect(await session.master.fire(uid, 2), isTrue);
    final sent = radio.packets.sublist(before);
    expect(sent.map((p) => p.command), [0x20]);
    expect(sent.single.data, [2]);
    expect(session.master.armed, {uid});
    expect(session.master.status, 'Send fire cue 2 complete');
  });

  test('fired cues stay marked until the module is disarmed', () async {
    await session.master.armModule(uid);
    expect(await session.master.fire(uid, 2), isTrue);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(await session.master.fire(uid, 7), isTrue);
    expect(session.master.firedCues[uid], {2, 7});
    expect(session.master.lastFire, (uid: uid, cue: 7));

    expect(await session.master.disarmModule(uid), isTrue);
    expect(session.master.firedCues[uid], isNull);
    expect(session.master.lastFire, isNull);

    session.master.interrupted = false;
    await session.master.armModule(uid);
    await Future<void>.delayed(const Duration(milliseconds: 200));
    expect(await session.master.fire(uid, 3), isTrue);
    await session.master.emergencyStop();
    expect(session.master.firedCues, isEmpty);
  });

  test('arm retries a lost output-status reply', () async {
    radio.dropStatusReplies = 1;
    expect(await session.master.armModule(uid), isTrue);
    expect(radio.packets.where((p) => p.command == 0x10), hasLength(1));
  });

  test('disarm retries a lost unique DISARM', () async {
    await session.master.armModule(uid);
    radio.dropDisarms = 2;
    expect(await session.master.disarmModule(uid), isTrue);
    expect(
      radio.packets.where((p) => p.command == 0x11 && p.target == uid),
      hasLength(3),
    );
    expect(radio.armed[uid], isFalse);
  });

  test('disarm all broadcasts DISARM before confirming each module', () async {
    await session.master.armModule(uid);
    final before = radio.packets.length;
    expect(await session.master.disarmAll(), isTrue);
    final sent = radio.packets.sublist(before);
    expect(
      sent.take(3).every((p) => p.command == 0x11 && p.target == null),
      isTrue,
    );
    expect(
      sent.where((p) => p.command == 0x11 && p.target == uid),
      hasLength(1),
    );
    expect(session.master.armed, isEmpty);
  });

  test('field startup fault NACK never confirms arm', () async {
    radio.reject = 0x10;
    expect(await session.master.armModule(uid), isFalse);
    expect(session.master.armed, isEmpty);
    expect(session.master.status, contains('rejected'));
  });

  test('manual fire preserves the original 180 ms debounce', () async {
    await session.master.armModule(uid);
    expect(await session.master.fire(uid, 1), isTrue);
    expect(await session.master.fire(uid, 1), isFalse);
    expect(radio.packets.where((p) => p.command == 0x20), hasLength(1));
  });

  test(
    'live operations cannot resume while STOP transmissions are pending',
    () async {
      radio.transmitGate = Completer<void>();
      final stop = session.master.emergencyStop();
      await Future<void>.delayed(Duration.zero);
      expect(session.master.stopping, isTrue);
      expect(await session.master.disarmModule(uid), isFalse);
      expect(await session.master.armModule(uid), isFalse);
      radio.transmitGate!.complete();
      await stop;
      expect(session.master.stopping, isFalse);
      expect(session.master.interrupted, isTrue);
    },
  );

  test('maintenance and script upload are inhibited while armed', () async {
    await session.master.armModule(uid);
    final count = radio.packets.length;
    expect(await session.master.uploadScript(script()), isFalse);
    expect(await session.master.setModuleValue(uid, 0x32, 1234), isFalse);
    expect(await session.master.updateModule(uid, 'main.py', [1]), isFalse);
    expect(radio.packets, hasLength(count));
    expect(session.canSetModuleGroup, isFalse);
  });

  test('script upload merges simultaneous cues and preserves cue 64', () async {
    final show = script('0.000,1,64,last\n0.000,1,1,first\n1.000,1,2,next');
    expect(await session.master.uploadScript(show), isTrue);
    expect(session.master.uploaded[uid], [
      (0, (BigInt.one << 63) | BigInt.one),
      (1000, BigInt.two),
    ]);
    expect(radio.packets.map((p) => p.command), [0x70, 0x71, 0x52]);
    expect(session.master.document, same(show));
  });

  test(
    'a LOAD is only resent after CLEAR and failure invalidates preparation',
    () async {
      radio.hold = 0x71;
      expect(await session.master.uploadScript(script()), isFalse);
      expect(session.master.document, isNull);
      expect(session.master.uploaded, isEmpty);
      final sent = radio.packets.map((p) => p.command).toList();
      expect(sent, [0x70, 0x71, 0x70, 0x71, 0x70, 0x71]);
      expect(await session.master.startScript(), isFalse);
    },
  );

  test(
    'a lost LOAD reply restarts the module without duplicate events',
    () async {
      final show = script('0.000,1,1,a\n1.000,1,2,b\n2.000,1,3,c\n3.000,1,4,d');
      radio.dropLoadReplies = 1;
      expect(await session.master.uploadScript(show), isTrue);
      expect(radio.packets.map((p) => p.command), [
        0x70,
        0x71,
        0x70,
        0x71,
        0x71,
        0x52,
      ]);
      expect(radio.scriptCounts[uid], 4);
      expect(session.master.uploaded[uid], hasLength(4));
    },
  );

  test(
    'an INFO count mismatch prevents prepared state after LOAD ACKs',
    () async {
      radio.wrongReadback = true;
      expect(await session.master.uploadScript(script()), isFalse);
      expect(session.master.document, isNull);
      expect(session.master.uploaded, isEmpty);
      expect(session.master.status, contains('readback'));
    },
  );

  test(
    'duplicate groups and field event capacity fail before transmission',
    () async {
      session.modules[2] = FieldModule(2, 1, 64, 0x20)..seenInScan = true;
      expect(await session.master.uploadScript(script()), isFalse);
      expect(radio.packets, isEmpty);
      session.modules.remove(2);
      final show = script('0.000,2,1,missing');
      expect(await session.master.uploadScript(show), isFalse);
      expect(radio.packets, isEmpty);
    },
  );

  test('step consumes events once and sends no timed START', () async {
    await session.master.uploadScript(script());
    expect(await session.master.startScript(step: true), isFalse);
    await session.master.armModule(uid);
    expect(await session.master.startScript(step: true), isTrue);
    expect(await session.master.nextStep(), isTrue);
    expect(await session.master.nextStep(), isTrue);
    expect(await session.master.nextStep(), isFalse);
    expect(radio.packets.where((p) => p.command == 0x76), hasLength(2));
    expect(radio.packets.where((p) => p.command == 0x72), isEmpty);
  });

  test(
    'timed script syncs clocks, starts and sends 200 ms heartbeats',
    () async {
      await session.master.uploadScript(script());
      await session.master.armModule(uid);
      expect(await session.master.startScript(), isTrue);
      expect(radio.packets.where((p) => p.command == 0x75), hasLength(4));
      expect(radio.packets.where((p) => p.command == 0x72), hasLength(1));
      await Future<void>.delayed(const Duration(milliseconds: 230));
      expect(radio.packets.where((p) => p.command == 0x74), isNotEmpty);
      await session.master.emergencyStop();
      expect(session.master.running, isFalse);
      expect(session.master.armed, isEmpty);
      expect(radio.packets.where((p) => p.command == 0x11), hasLength(3));
    },
  );

  test(
    'operator stop cancels a pending arm without later restoring it',
    () async {
      radio.hold = 0x10;
      final arm = session.master.armModule(uid);
      await Future<void>.delayed(Duration.zero);
      await session.master.emergencyStop();
      expect(await arm, isFalse);
      radio.reply(uid, 0x50, [0x10, 0]);
      await Future<void>.delayed(Duration.zero);
      expect(session.master.armed, isEmpty);
      expect(session.master.interrupted, isTrue);
    },
  );

  test('disconnect invalidates arm, upload and script heartbeats', () async {
    await session.master.uploadScript(script());
    await session.master.armModule(uid);
    await session.master.startScript();
    await session.disconnect();
    final count = radio.packets.length;
    await Future<void>.delayed(const Duration(milliseconds: 230));
    expect(radio.packets, hasLength(count));
    expect(session.master.armed, isEmpty);
    expect(session.master.document, isNull);
  });

  test(
    'worst-case escaped OTA chunks fit bridge MTU and offsets are exact',
    () async {
      expect(
        await session.master.updateModule(
          uid,
          'main.py',
          List.filled(161, 255),
        ),
        isTrue,
      );
      final writes = radio.packets.where((p) => p.command == 0x61).toList();
      expect(writes.map((p) => readUnsigned(p.data, 2, 4)), [0, 80, 160]);
      for (final request in radio.bridge.where((p) => p.command == 0x10)) {
        expect(request.payload.length, lessThanOrEqualTo(218));
      }
    },
  );

  test('OTA rejection sends abort and never reboot', () async {
    radio.reject = 0x61;
    expect(await session.master.updateModule(uid, 'main.py', [1, 2]), isFalse);
    expect(radio.packets.map((p) => p.command), [0x60, 0x61, 0x63]);
    expect(await session.master.updateModule(uid, '../main.py', [1]), isFalse);
  });

  test('bridge update uses original CRC16 envelope', () async {
    expect(
      await session.master.updateBridge('main.py', List.filled(100, 255)),
      isTrue,
    );
    expect(radio.bridge.map((p) => p.command), [0x32, 0x33, 0x33, 0x33, 0x34]);
    expect(
      readUnsigned(
        radio.bridge.first.payload,
        radio.bridge.first.payload.length - 2,
        2,
      ),
      crc16(List.filled(100, 255)),
    );
  });

  test('arm all and disarm all ACK every discovered module', () async {
    const second = 0x0000beef;
    session.modules[second] = FieldModule(second, 2, 64, 0x20)
      ..seenInScan = true;
    expect(await session.master.armAll(), isTrue);
    expect(session.master.armed, {uid, second});
    expect(
      radio.packets.where((p) => p.command == 0x10).map((p) => p.target),
      containsAll([uid, second]),
    );
    expect(await session.master.disarmAll(), isTrue);
    expect(session.master.armed, isEmpty);
    expect(radio.armed.values, everyElement(isFalse));
  });

  test('arm all reports modules that did not confirm ARM', () async {
    radio.reject = 0x10;
    expect(await session.master.armAll(), isFalse);
    expect(session.master.armed, isEmpty);
    expect(session.master.status, contains('ARM not confirmed for 1'));
  });

  test('arm all requires discovered modules', () async {
    session.modules[uid]!.seenInScan = false;
    expect(await session.master.armAll(), isFalse);
    expect(radio.packets, isEmpty);
  });

  test('confirmed manual fire records the last fired cue', () async {
    await session.master.armModule(uid);
    expect(session.master.lastFire, isNull);
    expect(await session.master.fire(uid, 3), isTrue);
    expect(session.master.lastFire, (uid: uid, cue: 3));
    session.master.invalidate();
    expect(session.master.lastFire, isNull);
  });

  group('script runner', () {
    ScriptDocument show() =>
        script('0.000,1,64,first\n1.000,1,1,second\n2.000,1,2,third');

    Future<void> startRun() async {
      expect(await session.master.uploadScript(show()), isTrue);
      expect(await session.master.armScriptModules(), isTrue);
      expect(await session.master.startScript(), isTrue);
    }

    int startOffset(NfcpPacket start) => readUnsigned(start.data, 4, 4);

    test('pause stops every module, keeps it armed and resumes after '
        'the last fired event', () async {
      await startRun();
      radio.fireSequences[uid] = 1; // the field fired the 0 ms event
      final before = radio.packets.length;
      expect(await session.master.pauseScript(), isTrue);
      final sent = radio.packets.sublist(before);
      expect(
        sent.where((p) => p.command == 0x73 && p.target == null),
        hasLength(3),
      );
      expect(
        sent.where((p) => p.command == 0x73 && p.target == uid),
        hasLength(1),
      );
      expect(sent.where((p) => p.command == 0x11), isEmpty);
      expect(radio.scriptRunning[uid], isFalse);
      expect(session.master.running, isFalse);
      expect(session.master.armed, {uid});
      // Resume lies after 0 ms (fired) and no later than 1000 ms (unfired).
      expect(session.master.pausedAtMs, inInclusiveRange(1, 1000));
      expect(session.master.skippedOnPause, 0);

      final heartbeats = radio.packets.where((p) => p.command == 0x74).length;
      await Future<void>.delayed(const Duration(milliseconds: 250));
      expect(
        radio.packets.where((p) => p.command == 0x74),
        hasLength(heartbeats),
      );

      final resumeAt = session.master.pausedAtMs!;
      expect(await session.master.startScript(offsetMs: resumeAt), isTrue);
      expect(session.master.paused, isFalse);
      expect(session.master.running, isTrue);
      final starts = radio.packets.where((p) => p.command == 0x72).toList();
      expect(starts, hasLength(2));
      expect(startOffset(starts.last), resumeAt);
      await session.master.emergencyStop();
    });

    test('pause after every event fired completes and disarms', () async {
      await startRun();
      radio.fireSequences[uid] = 3;
      expect(await session.master.pauseScript(), isTrue);
      expect(session.master.paused, isFalse);
      expect(session.master.completed, isTrue);
      expect(session.master.armed, isEmpty);
      expect(radio.armed[uid], isFalse);
      expect(session.master.interrupted, isFalse);
    });

    test('a finished run disarms every module and confirms it', () async {
      session.master
        ..startLeadMs = 1100
        ..startLeadPerModuleMs = 0;
      expect(
        await session.master.uploadScript(script('0.000,1,3,only')),
        isTrue,
      );
      expect(await session.master.armScriptModules(), isTrue);
      expect(await session.master.startScript(), isTrue);
      // The field fires its single event.
      radio.fireSequences[uid] = 1;
      radio.lastFireMasks[uid] = BigInt.from(4);
      final deadline = DateTime.now().add(const Duration(seconds: 6));
      while (session.master.running && DateTime.now().isBefore(deadline)) {
        await Future<void>.delayed(const Duration(milliseconds: 50));
      }
      await Future<void>.delayed(const Duration(milliseconds: 200));
      expect(session.master.running, isFalse);
      expect(session.master.completed, isTrue);
      expect(session.master.armed, isEmpty);
      expect(session.master.interrupted, isFalse);
      expect(radio.armed[uid], isFalse);
      expect(
        radio.packets.where((p) => p.command == 0x11 && p.target == uid),
        hasLength(1),
      );
      expect(session.master.status, 'Script complete; all modules disarmed');
    });

    test('a module reporting extra outputs stops and disarms all', () async {
      await startRun();
      radio.fireSequences[uid] = 9;
      expect(await session.master.pauseScript(), isFalse);
      expect(session.master.armed, isEmpty);
      expect(session.master.paused, isFalse);
      expect(radio.packets.where((p) => p.command == 0x11), isNotEmpty);
    });

    test(
      'stop ends a paused run without disarming; start begins at 0',
      () async {
        await startRun();
        radio.fireSequences[uid] = 1;
        await session.master.pauseScript();
        expect(await session.master.stopScript(), isTrue);
        expect(session.master.paused, isFalse);
        expect(session.master.armed, {uid});
        expect(await session.master.startScript(), isTrue);
        expect(
          startOffset(radio.packets.lastWhere((p) => p.command == 0x72)),
          0,
        );
        await session.master.emergencyStop();
      },
    );

    test('stop halts a running script and keeps the modules armed', () async {
      await startRun();
      expect(await session.master.stopScript(), isTrue);
      expect(session.master.running, isFalse);
      expect(radio.scriptRunning[uid], isFalse);
      expect(session.master.armed, {uid});
      expect(await session.master.stopScript(), isFalse);
    });

    test('arm arms only the modules holding the script', () async {
      const other = 0x0000beef;
      session.modules[other] = FieldModule(other, 2, 64, 0x20)
        ..seenInScan = true;
      expect(await session.master.armScriptModules(), isFalse);
      await session.master.uploadScript(show());
      expect(await session.master.armScriptModules(), isTrue);
      expect(session.master.armed, {uid});
      expect(radio.armed[other], isNull);
    });

    test('stop all and disarm halts the run and confirms disarm', () async {
      await startRun();
      expect(await session.master.stopAllAndDisarm(), isTrue);
      expect(session.master.running, isFalse);
      expect(session.master.armed, isEmpty);
      expect(session.master.interrupted, isFalse);
      expect(radio.armed[uid], isFalse);
      expect(radio.packets.where((p) => p.command == 0x73), isNotEmpty);
      expect(
        radio.packets.where((p) => p.command == 0x11 && p.target == uid),
        hasLength(1),
      );
    });
  });

  testWidgets('script runner walks send, arm, start, pause, resume and stop', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1280, 1600));
    final document = script('0.000,1,64,first\n1.000,1,1,second');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: ListenableBuilder(
              listenable: session,
              builder: (_, _) =>
                  ScriptRunnerPanel(session: session, script: document),
            ),
          ),
        ),
      ),
    );
    bool enabled(String label) {
      final button = tester.widget<ButtonStyleButton>(
        find
            .ancestor(
              of: find.text(label),
              matching: find.byWidgetPredicate((w) => w is ButtonStyleButton),
            )
            .first,
      );
      return button.onPressed != null;
    }

    Future<void> press(String label) async {
      await tester.tap(find.text(label));
      // Radio I/O runs on real time; settle delays run on the test clock.
      for (var i = 0; i < 20; i++) {
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 10)),
        );
        await tester.pump(const Duration(milliseconds: 5));
      }
      expect(tester.takeException(), isNull, reason: label);
    }

    expect(find.text('NOT SENT'), findsOneWidget);
    expect(find.text('STOP ALL & DISARM'), findsOneWidget);
    expect(enabled('Start'), isFalse);
    expect(enabled('Arm'), isFalse);
    await press('Send to modules');
    expect(find.text('READY'), findsOneWidget);
    expect(enabled('Start'), isFalse);
    await press('Arm');
    expect(find.text('ARMED'), findsOneWidget);
    expect(enabled('Send to modules'), isFalse);
    expect(enabled('Start'), isTrue);
    await press('Start');
    expect(find.text('RUNNING'), findsOneWidget);
    expect(enabled('Pause'), isTrue);
    radio.fireSequences[uid] = 1;
    await press('Pause');
    expect(find.text('PAUSED'), findsOneWidget);
    expect(find.text('Resume'), findsOneWidget);
    await press('Resume');
    expect(find.text('RUNNING'), findsOneWidget);
    await press('Stop');
    expect(find.text('ARMED'), findsOneWidget);
    await press('STOP ALL & DISARM');
    expect(session.master.armed, isEmpty);
    expect(find.text('READY'), findsOneWidget);
    await tester.binding.setSurfaceSize(null);
  });

  testWidgets('manual fire grid is disabled until armed, then fires a cue', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1280, 1600));
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: ListenableBuilder(
              listenable: session,
              builder: (_, _) => ManualFirePanel(session: session),
            ),
          ),
        ),
      ),
    );
    final cue5 = find.bySemanticsLabel('Fire cue 5');
    expect(cue5, findsOneWidget);
    expect(find.text('Manual fire disabled - not armed'), findsOneWidget);
    await tester.tap(cue5);
    await tester.pump();
    expect(radio.packets.where((p) => p.command == 0x20), isEmpty);

    await tester.runAsync(session.master.armAll);
    await tester.pump();
    expect(find.text('Manual fire disabled - not armed'), findsNothing);

    await tester.tap(cue5);
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 50)),
    );
    await tester.pump();
    final fire = radio.packets.where((p) => p.command == 0x20).single;
    expect(fire.target, uid);
    expect(fire.data, [0x10]);
    expect(session.master.lastFire, (uid: uid, cue: 5));
    expect(find.text('Last sent: 0x12FF5678 cue 5'), findsOneWidget);
    await tester.binding.setSurfaceSize(null);
  });

  testWidgets('module tools live in the module card Settings, with RSSI', (
    tester,
  ) async {
    await tester.binding.setSurfaceSize(const Size(1280, 800));
    session.modules[uid]!.rssi = -61;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListenableBuilder(
            listenable: session,
            builder: (_, _) => SingleChildScrollView(
              child: FieldModulesPanel(session: session),
            ),
          ),
        ),
      ),
    );
    // Signal strength sits beside the battery in the module header.
    expect(find.text('-61 dBm'), findsOneWidget);
    await openModules(tester);
    expect(find.text('Speed test'), findsNothing);
    await tester.ensureVisible(find.text('Settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Diagnostics and maintenance'), findsOneWidget);
    for (final label in [
      'Speed test',
      'Range test (20 pings)',
      'Raw ADC',
      'Calibration',
      'Set unique ID',
      'Set network',
      'Set channel',
      'Reboot module',
    ]) {
      final button = find.widgetWithText(OutlinedButton, label);
      expect(button, findsOneWidget, reason: label);
      expect(tester.widget<OutlinedButton>(button).onPressed, isNotNull);
    }
    expect(find.text('Physical output state: not read'), findsOneWidget);
    await tester.ensureVisible(find.text('Refresh physical output state'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh physical output state'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Physical outputs: disarmed'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.binding.setSurfaceSize(null);
  });

  testWidgets('radio tools live on the Radio page', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(children: [RadioMaintenance(session: session)]),
        ),
      ),
    );
    await tester.tap(find.text('Radio settings and maintenance'));
    await tester.pumpAndSettle();
    for (final label in [
      'Set radio network',
      'Set radio channel',
      'Scan channels',
      'Clear radio statistics',
      'Reset radio',
    ]) {
      expect(find.widgetWithText(OutlinedButton, label), findsOneWidget);
    }
    // Nothing to offer on monitor firmware.
    session.values['Firmware'] = 'usb-monitor-0.3.1';
    await tester.pumpWidget(
      MaterialApp(home: Scaffold(body: RadioMaintenance(session: session))),
    );
    expect(find.text('Radio settings and maintenance'), findsNothing);
  });

}
