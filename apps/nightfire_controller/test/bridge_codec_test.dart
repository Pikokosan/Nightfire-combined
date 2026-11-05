import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';

void main() {
  test('Console diagnostics are separate from CRC-validated bridge frames', () {
    final lines = <String>[];
    final decoder = UsbLineDecoder(onConsole: lines.add);
    expect(decoder.add(ascii.encode('USB monitor error: test\r\n')), isEmpty);
    expect(lines, ['USB monitor error: test']);
    expect(
      decoder
          .add(usbLine(const BridgeFrame(4, 2, [], flags: 2)))
          .single
          .command,
      2,
    );
    expect(lines.length, 1);
  });
  test('CCITT reference vector', () {
    expect(crc16(ascii.encode('123456789')), 0x29b1);
  });
  test('Python bridge framing golden packet', () {
    expect(const BridgeFrame(1, 1, []).encode(), [
      0xaa,
      0x55,
      4,
      1,
      0,
      1,
      1,
      0xcd,
      0xae,
    ]);
  });
  test('Fragmented input, CRLF and console noise', () {
    final decoder = UsbLineDecoder();
    final encoded = usbLine(const BridgeFrame(7, 3, [0xff, 0x03], flags: 2));
    expect(decoder.add(ascii.encode('MicroPython console\r\n')), isEmpty);
    expect(decoder.add(encoded.sublist(0, 13)), isEmpty);
    final result = decoder.add(encoded.sublist(13));
    expect(result.single.sequence, 7);
    expect(result.single.payload, [0xff, 0x03]);
  });
  test('Bad CRC, malformed hex and oversized lines', () {
    final decoder = UsbLineDecoder();
    final corrupt = const BridgeFrame(1, 3, []).encode();
    corrupt[7] ^= 1;
    final hex = corrupt
        .map((value) => value.toRadixString(16).padLeft(2, '0'))
        .join();
    expect(
      decoder.add(ascii.encode('NFUSB1 $hex\nNFUSB1 zzz\n${'a' * 600}\n')),
      isEmpty,
    );
    expect(
      decoder
          .add(usbLine(const BridgeFrame(8, 2, [], flags: 2)))
          .single
          .sequence,
      8,
    );
  });
}
