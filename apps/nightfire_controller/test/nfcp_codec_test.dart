import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';

String hex(List<int> data) =>
    data.map((value) => value.toRadixString(16).padLeft(2, '0')).join();

void main() {
  test('Legacy continuity does not invent readings for unreported cues', () {
    final reading = ResistanceSnapshot.parseContinuity([0, 0, 1, 0], 24);
    expect(reading.continuityOnly, isTrue);
    expect(reading.cues.first.label, 'Open');
    expect(reading.cues[15].label, 'Connected');
    expect(reading.cues[16].label, 'Invalid');
    expect(
      () => ResistanceSnapshot.parseContinuity([], 24),
      throwsFormatException,
    );
  });
  test('Golden query frames match the existing Python NFCP encoder', () {
    expect(hex(discoverQuery(0xff55)), 'fffd040140fffe55fabc');
    expect(hex(moduleQuery(0x12ff5678, 0x52)), 'fffb060112fffe5678520918');
    expect(hex(moduleQuery(0x12ff5678, 0x24)), 'fffb060112fffe5678241749');
    expect(() => moduleQuery(1, 0x10), throwsArgumentError);
  });
  test('Response escaping, CRC and exact frame boundaries', () {
    final frame = encodeNfcp(0xfa, [0x12, 0xff, 0x56, 0x78, 0x53, 0xff]);
    final packet = decodeNfcp(frame)!;
    expect(packet.source, 0x12ff5678);
    expect(packet.data, [255]);
    expect(decodeNfcp([...frame, 0]), isNull);
    expect(decodeNfcp(frame.sublist(0, frame.length - 1)), isNull);
    frame.last ^= 1;
    expect(decodeNfcp(frame), isNull);
  });
  test('Resistance differentiates connected, short, open and invalid', () {
    final snapshot = ResistanceSnapshot.parse([
      1,
      1,
      4,
      2,
      0,
      25,
      0,
      0,
      255,
      255,
      255,
      254,
    ], 4);
    expect(snapshot.status, 2);
    expect(snapshot.cues.map((cue) => cue.label), [
      'Connected',
      'Short',
      'Open',
      'Invalid',
    ]);
    expect(snapshot.cues.first.detail, '2.5 Ω');
    expect(
      ResistanceSnapshot.parse([1, 2, 1, 0, 0, 25], 1).cues.single.label,
      'Raw',
    );
    expect(
      () => ResistanceSnapshot.parse([1, 1, 4, 0, 0, 1], 4),
      throwsFormatException,
    );
    expect(
      () => ResistanceSnapshot.parse([1, 9, 1, 0, 0, 1], 1),
      throwsFormatException,
    );
  });
  test(
    'Module info accepts optional older fields but rejects truncated strings',
    () {
      expect(moduleInfo([3, ...utf8.encode('nrf')])['App'], 'nrf');
      expect(() => moduleInfo([5, 65]), throwsFormatException);
    },
  );
}
