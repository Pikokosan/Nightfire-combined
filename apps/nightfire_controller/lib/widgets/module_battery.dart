import 'package:flutter/material.dart';

import '../settings/battery_settings.dart';

class ModuleBattery extends StatelessWidget {
  const ModuleBattery({
    super.key,
    required this.millivolts,
    this.packType = kDefaultPackType,
    this.calibrated = false,
  });

  final int? millivolts;
  final String packType;
  final bool calibrated;

  int get _cells => cellCount(packType) ?? 2;
  int get emptyMv => _cells * kEmptyCellMv;
  int get fullMv => _cells * kFullCellMv;
  double? get level => millivolts == null || millivolts! <= 0
      ? null
      : ((millivolts! - emptyMv) / (fullMv - emptyMv)).clamp(0.0, 1.0);
  int? get percent => level == null ? null : (level! * 100).round();

  @override
  Widget build(BuildContext context) {
    final value = level;
    final color = value == null
        ? Colors.blueGrey
        : value <= 0.2
        ? Colors.redAccent
        : value <= 0.5
        ? Colors.amber
        : Colors.lightGreen;
    final label = value == null
        ? 'Battery voltage unavailable'
        : 'Battery ${(millivolts! / 1000).toStringAsFixed(2)} V • $percent% voltage estimate • '
              '$packType ${(emptyMv / 1000).toStringAsFixed(1)}–${(fullMv / 1000).toStringAsFixed(1)} V scale'
              '${calibrated ? ' • calibrated' : ''}';
    return Semantics(
      label: label,
      excludeSemantics: true,
      child: Tooltip(
        message: label,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (value == null)
              Icon(Icons.battery_unknown, size: 28, color: color)
            else
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Container(
                    width: 30,
                    height: 17,
                    padding: const EdgeInsets.all(2),
                    decoration: BoxDecoration(
                      border: Border.all(color: color, width: 1.5),
                      borderRadius: BorderRadius.circular(3),
                    ),
                    child: Align(
                      alignment: Alignment.centerLeft,
                      child: FractionallySizedBox(
                        widthFactor: value,
                        heightFactor: 1,
                        child: ColoredBox(color: color),
                      ),
                    ),
                  ),
                  Container(width: 3, height: 8, color: color),
                ],
              ),
            const SizedBox(width: 6),
            Text(
              value == null
                  ? '—'
                  : '$percent% • ${(millivolts! / 1000).toStringAsFixed(2)} V',
              style: TextStyle(color: color, fontSize: 14),
            ),
          ],
        ),
      ),
    );
  }
}

/// Battery indicator for a module's INFO fields, using the app's battery
/// settings for that module's firmware target.
class ModuleBatteryForInfo extends StatelessWidget {
  const ModuleBatteryForInfo({super.key, required this.info});
  final Map<String, String> info;

  @override
  Widget build(BuildContext context) {
    final reading = BatterySettingsScope.of(context).read(info);
    return ModuleBattery(
      millivolts: reading.millivolts,
      packType: reading.packType,
      calibrated: reading.calibrated,
    );
  }
}
