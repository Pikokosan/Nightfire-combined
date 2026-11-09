import '../protocol/bridge_codec.dart';
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

  /// Mesh hops of the last frame heard from the module (0 = direct).
  int? hops;

  /// How the bridge last delivered a request to it (mesh bridges only).
  ModuleRoute? route;
  int? relay;

  /// Delivered requests by the route the reply took, and failed ones (mesh
  /// bridges only). Shown on the Mesh page.
  final routeCounts = <ModuleRoute, int>{};
  int failedDeliveries = 0;

  /// The last delivery: attempts used, when it ended, and whether it failed.
  int? attempts;
  DateTime? routeAt;
  bool lastDeliveryFailed = false;

  /// "Direct", "Via 0x…", or "Flooded", once known.
  String? get routeText => switch (route) {
    ModuleRoute.direct => 'Direct',
    ModuleRoute.relayed =>
      'Via 0x${(relay ?? 0).toRadixString(16).padLeft(8, '0').toUpperCase()}',
    ModuleRoute.flooded => 'Flooded',
    null =>
      hops == null
          ? null
          : (hops == 0 ? 'Direct' : '$hops hop${hops == 1 ? '' : 's'}'),
  };
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

  /// Bumped when an automatic continuity reply arrives; the module card
  /// flashes green on each change.
  int replyFlash = 0;

  /// The last continuity check got no reply; its light shows red until a
  /// check succeeds.
  bool noReply = false;
}
