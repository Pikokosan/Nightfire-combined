import 'dart:convert';
import 'dart:typed_data';

int crc16(List<int> bytes) {
  var checksum = 0xffff;
  for (final value in bytes) {
    checksum ^= value << 8;
    for (var bit = 0; bit < 8; bit++) {
      checksum =
          ((checksum & 0x8000) != 0
              ? (checksum << 1) ^ 0x1021
              : checksum << 1) &
          0xffff;
    }
  }
  return checksum;
}

class BridgeFrame {
  const BridgeFrame(
    this.sequence,
    this.command,
    this.payload, {
    this.flags = 0,
  });
  final int sequence;
  final int command;
  final List<int> payload;
  final int flags;

  Uint8List encode() {
    if (payload.length > 220) throw ArgumentError('Payload exceeds 220 bytes');
    final body = [
      payload.length + 4,
      1,
      flags,
      sequence & 255,
      command,
      ...payload,
    ];
    final checksum = crc16(body);
    return Uint8List.fromList([
      0xaa,
      0x55,
      ...body,
      checksum >> 8,
      checksum & 255,
    ]);
  }

  static BridgeFrame? decode(List<int> data) {
    if (data.length < 9 || data[0] != 0xaa || data[1] != 0x55) return null;
    final length = data[2];
    if (length < 4 ||
        length > 224 ||
        data.length != length + 5 ||
        data[3] != 1) {
      return null;
    }
    final checksum = (data[data.length - 2] << 8) | data.last;
    if (crc16(data.sublist(2, data.length - 2)) != checksum) return null;
    return BridgeFrame(
      data[5],
      data[6],
      data.sublist(7, data.length - 2),
      flags: data[4],
    );
  }
}

Uint8List usbLine(BridgeFrame frame) => Uint8List.fromList(
  ascii.encode(
    'NFUSB1 ${frame.encode().map((value) => value.toRadixString(16).padLeft(2, '0')).join()}\n',
  ),
);

class UsbLineDecoder {
  UsbLineDecoder({this.onConsole});
  final void Function(String)? onConsole;
  final List<int> _line = [];
  bool _discard = false;

  List<BridgeFrame> add(List<int> bytes) {
    final frames = <BridgeFrame>[];
    for (final value in bytes) {
      if (value == 10 || value == 13) {
        if (!_discard) {
          final text = ascii.decode(_line, allowInvalid: true);
          if (text.startsWith('NFUSB1 ')) {
            final hex = text.substring(7);
            if (hex.length.isEven && RegExp(r'^[0-9a-fA-F]+$').hasMatch(hex)) {
              final data = <int>[];
              for (var offset = 0; offset < hex.length; offset += 2) {
                data.add(
                  int.parse(hex.substring(offset, offset + 2), radix: 16),
                );
              }
              final frame = BridgeFrame.decode(data);
              if (frame != null) frames.add(frame);
            }
          } else if (text.isNotEmpty) {
            onConsole?.call(text);
          }
        }
        _line.clear();
        _discard = false;
      } else if (!_discard) {
        _line.add(value);
        if (_line.length > 512) {
          _line.clear();
          _discard = true;
        }
      }
    }
    return frames;
  }
}

int readUnsigned(List<int> bytes, int offset, int count) {
  var result = 0;
  for (var index = offset; index < offset + count; index++) {
    result = (result << 8) | bytes[index];
  }
  return result;
}

Map<String, String> bridgeInfo(List<int> bytes) {
  if (bytes.length < 8) throw const FormatException('Short bridge info');
  final versionLength = bytes.length > 8 ? bytes[8] : 0;
  if (versionLength > 0 && bytes.length < 9 + versionLength) {
    throw const FormatException('Truncated firmware version');
  }
  return {
    'Radio ID': readUnsigned(
      bytes,
      0,
      4,
    ).toRadixString(16).padLeft(8, '0').toUpperCase(),
    'Network':
        '0x${readUnsigned(bytes, 6, 2).toRadixString(16).padLeft(4, '0').toUpperCase()}',
    'Firmware': versionLength == 0
        ? 'Unknown'
        : utf8.decode(
            bytes.sublist(9, 9 + versionLength),
            allowMalformed: true,
          ),
  };
}

Map<String, String> bridgeStats(List<int> bytes) {
  if (bytes.length < 34) throw const FormatException('Short bridge statistics');
  final rawRssi = readUnsigned(bytes, 26, 2);
  return {
    'Channel': '${bytes[24]}',
    'TX packets': '${readUnsigned(bytes, 0, 4)}',
    'RX packets': '${readUnsigned(bytes, 8, 4)}',
    'RX CRC errors': '${readUnsigned(bytes, 16, 4)}',
    'Last RSSI': readUnsigned(bytes, 8, 4) == 0
        ? 'No received packets'
        : '${rawRssi >= 32768 ? rawRssi - 65536 : rawRssi} dBm (last sample)',
  };
}

/// Any 0.4.x USB master build; 0.4.1 added firmware updates with rollback.
bool isMasterFirmware(String? firmware) =>
    firmware != null && RegExp(r'^usb-master-0\.4\.\d+$').hasMatch(firmware);

/// Mesh bridges (usb-master 0.4.2+, usb-monitor 0.3.3+) deliver unicast
/// requests themselves: on the module's known route, retried with the same
/// mesh message id (the module answers a retry from its reply cache, never
/// running a command twice), then flooded.
bool supportsReliableDelivery(String? firmware) {
  final match = RegExp(r'^usb-(master|monitor)-(\d+)\.(\d+)\.(\d+)').firstMatch(firmware ?? '');
  if (match == null) return false;
  final version = [for (var i = 2; i <= 4; i++) int.parse(match.group(i)!)];
  final minimum = match.group(1) == 'master' ? [0, 4, 2] : [0, 3, 3];
  for (var i = 0; i < 3; i++) {
    if (version[i] != minimum[i]) return version[i] > minimum[i];
  }
  return true;
}

/// Bridge command 0x1A payload: uid(4) timeout_ms(2) NFCP frame.
List<int> reliablePayload(int uid, Duration timeout, List<int> frame) {
  final ms = timeout.inMilliseconds.clamp(0, 0xffff);
  return [
    (uid >> 24) & 0xff,
    (uid >> 16) & 0xff,
    (uid >> 8) & 0xff,
    uid & 0xff,
    (ms >> 8) & 0xff,
    ms & 0xff,
    ...frame,
  ];
}

/// How a module was reached.
enum ModuleRoute { direct, relayed, flooded }

/// The bridge's answer to 0x1A once the delivery has finished.
class Delivery {
  const Delivery(this.delivered, this.route, this.relay, this.attempts);
  final bool delivered;
  final ModuleRoute route;
  final int relay;
  final int attempts;

  static Delivery? parse(List<int> bytes) {
    if (bytes.length < 7 || bytes[1] > 2) return null;
    return Delivery(
      bytes[0] == 0,
      ModuleRoute.values[bytes[1]],
      readUnsigned(bytes, 2, 4),
      bytes[6],
    );
  }
}

/// Bridge files the firmware page may replace. The radio config is left out
/// so an update keeps its network, channel and LED settings.
const bridgeFirmwareFiles = [
  'usb_master_bundle.mpy',
  'bridge_radio.py',
  'mesh.py',
  'nfcp_frame.py',
  'usb_master_control.py',
  'usb_master_maintenance.py',
  'usb_bridge_transport.py',
  'usb_monitor_nfcp.py',
  'command_interface.py',
  'main.py', // Last: the boot entry only changes once everything else is in.
];

/// The version string compiled into a bridge image, if it has one.
String? embeddedBridgeVersion(List<int> bytes) => RegExp(
  r'usb-master-\d+\.\d+\.\d+',
).firstMatch(latin1.decode(bytes))?.group(0);

bool _atLeast(List<int> version, List<int> minimum) {
  for (var i = 0; i < minimum.length; i++) {
    if (version[i] != minimum[i]) return version[i] > minimum[i];
  }
  return true;
}

List<int>? _version(String? text, RegExp pattern) {
  final match = pattern.firstMatch(text ?? '');
  if (match == null) return null;
  return [for (var i = 1; i <= 3; i++) int.parse(match.group(i)!)];
}

/// usb-master 0.4.7+ passes the chase SCRIPT_START and the audio box
/// commands (audio_box/design.md); older bridges refuse them.
bool bridgeSupportsChase(String? firmware) {
  final version = _version(firmware, RegExp(r'^usb-master-(\d+)\.(\d+)\.(\d+)'));
  return version != null && _atLeast(version, [0, 4, 7]);
}

/// Module firmware that follows an audio box: field-module 0.3.3+ and
/// nrf-dummy 0.2.3+. `app` and `firmware` are the module's INFO fields.
bool moduleSupportsChase(String? app, String? firmware) {
  final minimum = switch (app) {
    'field-module' => [0, 3, 3],
    'nrf-dummy' => [0, 2, 3],
    _ => null,
  };
  final version = _version(firmware, RegExp(r'^(\d+)\.(\d+)\.(\d+)$'));
  return minimum != null && version != null && _atLeast(version, minimum);
}
