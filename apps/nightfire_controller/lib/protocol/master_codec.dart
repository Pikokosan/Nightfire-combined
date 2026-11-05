import 'dart:convert';

import 'nfcp_codec.dart';

/// Wire-compatible with main/nfcp.py. Masks use BigInt to preserve cue 64.
List<int> maskBytes(BigInt mask, [int? width]) {
  if (mask <= BigInt.zero || mask.bitLength > 64) {
    throw ArgumentError('Cue mask must contain cues 1–64');
  }
  final count = width ?? (mask.bitLength + 7) ~/ 8;
  return List.generate(
    count,
    (i) => ((mask >> ((count - i - 1) * 8)) & BigInt.from(255)).toInt(),
  );
}

List<int> masterUnique(int uid, int command, [List<int> data = const []]) {
  if (uid < 0 || uid > 0xffffffff) throw ArgumentError('Invalid UID');
  final frame = encodeNfcp(0xfb, [1, ...bigEndian(uid, 4), command, ...data]);
  if (frame.length > 218) {
    throw ArgumentError('Escaped packet exceeds bridge MTU');
  }
  return frame;
}

List<int> masterBroadcast(int command, [List<int> data = const []]) =>
    encodeNfcp(0xfd, [1, command, ...data]);

List<int> scriptChunk(
  int uid,
  int id,
  int chunk,
  List<(int, BigInt)> entries,
) => masterUnique(uid, 0x71, [
  ...bigEndian(id, 2),
  chunk,
  entries.length,
  for (final entry in entries) ...[
    ...bigEndian(entry.$1, 4),
    ...maskBytes(entry.$2, 8),
  ],
]);

List<int> otaBegin(int uid, int transfer, String path, List<int> bytes) {
  final name = utf8.encode(path);
  if (name.isEmpty ||
      name.length > 64 ||
      path.contains('..') ||
      path.startsWith('/') ||
      path.contains('\\') ||
      path.contains('\u0000')) {
    throw ArgumentError('Use a relative module path of 1–64 UTF-8 bytes');
  }
  final checksum = bytes.fold<int>(0, (sum, byte) => (sum + byte) & 0xffffffff);
  return masterUnique(uid, 0x60, [
    ...bigEndian(transfer, 2),
    ...bigEndian(bytes.length, 4),
    ...bigEndian(checksum, 4),
    name.length,
    ...name,
  ]);
}
