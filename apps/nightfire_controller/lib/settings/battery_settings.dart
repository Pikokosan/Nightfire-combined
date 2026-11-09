import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Lithium pack sizes the app can show. Empty is 3.2 V per cell, full 4.2 V.
const kPackTypes = ['1S', '2S', '3S', '4S'];
const kDefaultPackType = '2S';
const kEmptyCellMv = 3200;
const kFullCellMv = 4200;

/// Firmware targets that always get a settings card, as reported in the
/// module's INFO "App" field.
const kKnownTargets = {
  'field-module': 'Field module',
  'nrf-dummy': 'Dummy module',
};

int? cellCount(String? packType) {
  final match = RegExp(
    r'^\s*(\d+)\s*S\s*$',
    caseSensitive: false,
  ).firstMatch(packType ?? '');
  final cells = match == null ? null : int.parse(match.group(1)!);
  return cells == null || cells < 1 || cells > 12 ? null : cells;
}

/// Battery settings for one firmware target.
class BatteryProfile {
  const BatteryProfile({this.packType, this.scale = 1.0, this.offsetMv = 0});

  /// Pack type to show; null uses the type the module reports.
  final String? packType;

  /// Display correction: shown = reported * scale + offset.
  final double scale;
  final int offsetMv;

  bool get calibrated => scale != 1.0 || offsetMv != 0;

  BatteryProfile copyWith({
    String? Function()? packType,
    double? scale,
    int? offsetMv,
  }) => BatteryProfile(
    packType: packType == null ? this.packType : packType(),
    scale: scale ?? this.scale,
    offsetMv: offsetMv ?? this.offsetMv,
  );

  Map<String, Object?> toJson() => {
    'packType': packType,
    'scale': scale,
    'offsetMv': offsetMv,
  };

  static BatteryProfile fromJson(Object? json) {
    if (json is! Map) return const BatteryProfile();
    final pack = json['packType'];
    final scale = json['scale'];
    final offset = json['offsetMv'];
    return BatteryProfile(
      packType: pack is String && cellCount(pack) != null ? pack : null,
      scale: scale is num && scale > 0.5 && scale < 2 ? scale.toDouble() : 1.0,
      offsetMv: offset is num && offset.abs() <= 5000 ? offset.round() : 0,
    );
  }

  @override
  bool operator ==(Object other) =>
      other is BatteryProfile &&
      other.packType == packType &&
      other.scale == scale &&
      other.offsetMv == offsetMv;

  @override
  int get hashCode => Object.hash(packType, scale, offsetMv);
}

/// A module's battery voltage after applying its target's settings.
class BatteryReading {
  const BatteryReading({
    required this.reportedMv,
    required this.millivolts,
    required this.packType,
    required this.calibrated,
  });
  final int? reportedMv;
  final int? millivolts;
  final String packType;
  final bool calibrated;

  int get cells => cellCount(packType) ?? 2;
  int get emptyMv => cells * kEmptyCellMv;
  int get fullMv => cells * kFullCellMv;

  /// 0..1 voltage estimate, or null when there is no usable reading.
  double? get level => millivolts == null || millivolts! <= 0
      ? null
      : ((millivolts! - emptyMv) / (fullMv - emptyMv)).clamp(0.0, 1.0);
}

class BatterySettings extends ChangeNotifier {
  BatterySettings({Map<String, BatteryProfile>? profiles})
    : _profiles = {...?profiles};

  static const storageKey = 'battery_settings_v1';

  final Map<String, BatteryProfile> _profiles;

  BatteryProfile profileFor(String? target) =>
      _profiles[_key(target)] ?? const BatteryProfile();

  Iterable<String> get customisedTargets => _profiles.keys;

  static String _key(String? target) {
    final t = target?.trim() ?? '';
    return t.isEmpty ? 'unknown' : t;
  }

  void setProfile(String? target, BatteryProfile profile) {
    final key = _key(target);
    if (profile == const BatteryProfile()) {
      if (_profiles.remove(key) == null) return;
    } else {
      if (_profiles[key] == profile) return;
      _profiles[key] = profile;
    }
    notifyListeners();
    _save();
  }

  /// Applies this target's settings to a module's INFO fields.
  BatteryReading read(Map<String, String> info) {
    final profile = profileFor(info['App']);
    final reported = int.tryParse(info['Battery mV']?.trim() ?? '');
    final usable = reported != null && reported > 0;
    final reportedPack = info['Battery type'];
    return BatteryReading(
      reportedMv: usable ? reported : null,
      millivolts: usable
          ? (reported * profile.scale + profile.offsetMv).round()
          : null,
      packType:
          profile.packType ??
          (cellCount(reportedPack) != null
              ? reportedPack!.trim().toUpperCase()
              : kDefaultPackType),
      calibrated: profile.calibrated,
    );
  }

  /// Scale that makes [reportedMv] read as [meterMv]; keeps no offset.
  static double calibrationScale(int reportedMv, int meterMv) {
    if (reportedMv <= 0 || meterMv <= 0) {
      throw const FormatException('Both readings must be above zero.');
    }
    final scale = meterMv / reportedMv;
    if (scale <= 0.5 || scale >= 2) {
      throw const FormatException(
        'The meter reading is too far from the module reading. Check the meter and pack.',
      );
    }
    return scale;
  }

  static Future<BatterySettings> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final raw = prefs.getString(storageKey);
      if (raw == null) return BatterySettings();
      final decoded = jsonDecode(raw);
      if (decoded is! Map) return BatterySettings();
      return BatterySettings(
        profiles: {
          for (final entry in decoded.entries)
            if (entry.key is String)
              entry.key as String: BatteryProfile.fromJson(entry.value),
        },
      );
    } catch (_) {
      return BatterySettings();
    }
  }

  Future<void> _save() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(
        storageKey,
        jsonEncode({
          for (final entry in _profiles.entries)
            entry.key: entry.value.toJson(),
        }),
      );
    } catch (_) {
      // Settings still apply for this run; storage may be unavailable in tests.
    }
  }
}

/// Makes the app's [BatterySettings] available to widgets below it.
class BatterySettingsScope extends InheritedNotifier<BatterySettings> {
  const BatterySettingsScope({
    super.key,
    required BatterySettings settings,
    required super.child,
  }) : super(notifier: settings);

  static final _fallback = BatterySettings();

  static BatterySettings of(BuildContext context) =>
      context
          .dependOnInheritedWidgetOfExactType<BatterySettingsScope>()
          ?.notifier ??
      _fallback;
}
