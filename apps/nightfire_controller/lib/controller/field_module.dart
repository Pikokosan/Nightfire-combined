import '../protocol/nfcp_codec.dart';

class ModuleFocus {
  const ModuleFocus({this.uid, this.group, this.cue});
  final int? uid, group, cue;
  bool matches(FieldModule module) =>
      uid != null ? module.uid == uid : group == null || module.group == group;
}

class FieldModule {
  FieldModule(
    this.uid,
    this.group,
    this.cueCount,
    this.deviceType, {
    this.protocolVersion = 0,
  });
  final int uid;
  int group;
  final int cueCount;
  final int deviceType;
  final int protocolVersion;
  String get id => '0x${uid.toRadixString(16).padLeft(8, '0').toUpperCase()}';
  DateTime? lastSeen;
  bool seenInScan = false;
  int? rssi;
  Map<String, String> info = {};
  int? get scriptId {
    final match = RegExp(
      r'(?:^|\s)id=([0-9a-f]{4})(?=\s|$)',
      caseSensitive: false,
    ).firstMatch(info['Script'] ?? '');
    return match == null ? null : int.tryParse(match.group(1)!, radix: 16);
  }

  String get scriptStatus => scriptId == null ? 'Not loaded' : 'Loaded';
  ResistanceSnapshot? resistance;
  OutputStatus? outputStatus;
  DateTime? outputStatusAt;
  DateTime? measuredAt;
  String? error;
}
