import 'package:flutter/material.dart';

class ModuleBattery extends StatelessWidget {
  const ModuleBattery({super.key, required this.millivolts});
  final int? millivolts;
  double? get level => millivolts == null || millivolts! <= 0
      ? null
      : ((millivolts! - 6400) / 2000).clamp(0.0, 1.0);
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
        : 'Battery ${(millivolts! / 1000).toStringAsFixed(2)} V • $percent% voltage estimate • 6.4–8.4 V scale';
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
              value == null ? '—' : '$percent%',
              style: TextStyle(color: color, fontSize: 14),
            ),
          ],
        ),
      ),
    );
  }
}
