import 'dart:convert';
import 'dart:typed_data';

import 'bridge_codec.dart';

class NfcpPacket {
  const NfcpPacket(
    this.kind,
    this.source,
    this.command,
    this.data, {
    this.target,
  });
  final int kind;
  final int source;
  final int command;
  final List<int> data;
  final int? target;
}

Uint8List encodeNfcp(int kind, List<int> payload) {
  if (payload.length > 255) throw ArgumentError('NFCP payload too long');
  final body = [payload.length, ...payload];
  final checksum = crc16(body);
  final result = <int>[255, kind];
  for (final value in [...body, checksum >> 8, checksum & 255]) {
    result.add(value);
    if (value == 255) result.add(0xfe);
  }
  return Uint8List.fromList(result);
}

List<int> bigEndian(int value, int count) =>
    List.generate(count, (index) => (value >> ((count - index - 1) * 8)) & 255);

Uint8List discoverQuery(int nonce) =>
    encodeNfcp(0xfd, [1, 0x40, ...bigEndian(nonce, 2)]);

Uint8List moduleQuery(int uid, int command) {
  // 0x7C: an audio box's status (read-only, like the others).
  if (![0x22, 0x24, 0x27, 0x52, 0x7c].contains(command)) {
    throw ArgumentError('Unsupported diagnostic query');
  }
  return encodeNfcp(0xfb, [1, ...bigEndian(uid, 4), command]);
}

BigInt _readBigUnsigned(List<int> bytes, int offset, int count) {
  var value = BigInt.zero;
  for (var index = 0; index < count; index++) {
    value = (value << 8) | BigInt.from(bytes[offset + index]);
  }
  return value;
}

class OutputStatus {
  const OutputStatus({
    required this.armed,
    required this.scriptRunning,
    required this.startupFault,
    required this.driverEnabled,
    required this.cueCount,
    required this.activeMask,
    required this.lastFireMask,
    required this.fireSequence,
    required this.lastFireAgeMs,
    required this.scriptId,
  });
  final bool armed, scriptRunning, startupFault, driverEnabled;
  final int cueCount, fireSequence, lastFireAgeMs;
  final BigInt activeMask, lastFireMask;
  final int? scriptId;

  static OutputStatus parse(List<int> data) {
    if (data.length != 27 || data[0] != 1 || data[2] < 1 || data[2] > 64) {
      throw const FormatException('Invalid output status response');
    }
    final flags = data[1];
    final loaded = readUnsigned(data, 25, 2);
    return OutputStatus(
      armed: flags & 0x01 != 0,
      scriptRunning: flags & 0x02 != 0,
      startupFault: flags & 0x04 != 0,
      driverEnabled: flags & 0x08 != 0,
      cueCount: data[2],
      activeMask: _readBigUnsigned(data, 3, 8),
      lastFireMask: _readBigUnsigned(data, 11, 8),
      fireSequence: readUnsigned(data, 19, 4),
      lastFireAgeMs: readUnsigned(data, 23, 2),
      scriptId: loaded == 0xffff ? null : loaded,
    );
  }
}

Uint8List moduleGroupSetting(int uid, int group) {
  if (uid < 0 || uid > 0xffffffff || group < 0 || group > 65535) {
    throw ArgumentError('Invalid module UID or group number');
  }
  return encodeNfcp(0xfb, [
    1,
    ...bigEndian(uid, 4),
    0x31,
    ...bigEndian(group, 2),
  ]);
}

NfcpPacket? decodeNfcp(List<int> frame) {
  if (frame.length < 7 || frame[0] != 255) return null;
  final body = <int>[];
  var escaped = false;
  for (final value in frame.skip(2)) {
    if (escaped) {
      if (value != 0xfe) return null;
      body.add(255);
      escaped = false;
    } else if (value == 255) {
      escaped = true;
    } else {
      body.add(value);
    }
  }
  if (escaped || body.length < 5 || body.length != body.first + 3) return null;
  if (crc16(body.sublist(0, body.length - 2)) !=
      readUnsigned(body, body.length - 2, 2)) {
    return null;
  }
  final payload = body.sublist(1, body.length - 2);
  if (frame[1] == 0xfa && payload.length >= 5) {
    return NfcpPacket(
      0xfa,
      readUnsigned(payload, 0, 4),
      payload[4],
      payload.sublist(5),
    );
  }
  if (frame[1] == 0xfb && payload.length >= 6) {
    return NfcpPacket(
      0xfb,
      payload[0],
      payload[5],
      payload.sublist(6),
      target: readUnsigned(payload, 1, 4),
    );
  }
  if (frame[1] == 0xfd && payload.length >= 2) {
    return NfcpPacket(0xfd, payload[0], payload[1], payload.sublist(2));
  }
  return null;
}

Map<String, String> moduleInfo(List<int> data) {
  final result = <String, String>{};
  var offset = 0;
  for (final name in [
    'App',
    'Firmware',
    'Radio',
    'Script',
    'Hardware UID',
    'Configured UID',
    'Battery mV',
    'Battery type',
  ]) {
    if (offset == data.length) break;
    final count = data[offset++];
    if (offset + count > data.length) {
      throw const FormatException('Truncated module information');
    }
    result[name] = utf8.decode(
      data.sublist(offset, offset + count),
      allowMalformed: true,
    );
    offset += count;
  }
  return result;
}

class CueReading {
  const CueReading(this.label, this.detail);
  final String label;
  final String detail;
}

class ResistanceSnapshot {
  const ResistanceSnapshot(
    this.status,
    this.calibrated,
    this.cues, {
    this.continuityOnly = false,
  });
  final int status;
  final bool calibrated;
  final List<CueReading> cues;
  final bool continuityOnly;

  String get warning => [
    if (status & 1 != 0) 'Battery reading invalid',
    if (status & 2 != 0) 'ADC reading invalid',
    if (status & 4 != 0) 'Measurement fault detected',
    if (status & ~7 != 0) 'Unknown measurement warning',
  ].join('; ');

  static ResistanceSnapshot parseContinuity(List<int> data, int count) {
    if (count < 1 || count > 64) {
      throw const FormatException('Invalid cue count');
    }
    final expected = (count + 7) ~/ 8;
    final modern = data.length == expected;
    final legacy = !modern && data.length == 4;
    if (!modern && !legacy) {
      throw const FormatException('Invalid continuity response');
    }
    final mask = modern
        ? readUnsigned(data, 0, data.length)
        : readUnsigned(data, 1, 2);
    return ResistanceSnapshot(
      0,
      false,
      List.generate(count, (index) {
        if (legacy && index >= 16) {
          return const CueReading('Invalid', 'Not reported by legacy module');
        }
        return (mask >> index) & 1 == 1
            ? const CueReading('Open', 'Continuity only')
            : const CueReading('Connected', 'Continuity only');
      }),
      continuityOnly: true,
    );
  }

  static ResistanceSnapshot parse(List<int> data, int count) {
    if (count < 1 ||
        count > 64 ||
        data.length != 4 + count * 2 ||
        data[0] != 1 ||
        data[2] != count ||
        ![1, 2].contains(data[1])) {
      throw const FormatException('Invalid resistance response');
    }
    final calibrated = data[1] == 1;
    final cues = <CueReading>[];
    for (var index = 0; index < count; index++) {
      final value = readUnsigned(data, 4 + index * 2, 2);
      if (value == 0xfffe) {
        cues.add(const CueReading('Invalid', 'No valid measurement'));
      } else if (value == 0xffff) {
        cues.add(const CueReading('Open', 'Open circuit'));
      } else if (!calibrated) {
        cues.add(CueReading('Raw', '$value mV • uncalibrated'));
      } else if (value == 0) {
        cues.add(const CueReading('Short', '0.0 Ω'));
      } else {
        cues.add(
          CueReading('Connected', '${(value / 10).toStringAsFixed(1)} Ω'),
        );
      }
    }
    return ResistanceSnapshot(data[3], calibrated, cues);
  }
}

/// SHOW_TIME and AUDIO_STATUS states.
enum ShowState { stopped, playing, paused, ended }

/// An audio box's reply to AUDIO_STATUS_REQ (0x7C → 0x7D):
/// `track_id(2) position_ms(4) state(1) level(1) [length_ms(4)]`.
class AudioStatus {
  const AudioStatus(
    this.trackId,
    this.positionMs,
    this.state,
    this.level, [
    this.lengthMs,
  ]);
  final int trackId;
  final int positionMs;
  final ShowState state;
  final int level;
  final int? lengthMs;

  static AudioStatus parse(List<int> data) {
    if ((data.length != 8 && data.length != 12) || data[6] > 3) {
      throw const FormatException('Bad audio box status');
    }
    return AudioStatus(
      readUnsigned(data, 0, 2),
      readUnsigned(data, 2, 4),
      ShowState.values[data[6]],
      data[7],
      data.length == 12 ? readUnsigned(data, 8, 4) : null,
    );
  }
}
