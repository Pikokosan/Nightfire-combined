import 'package:flutter/material.dart';

import '../protocol/nfcp_codec.dart';

/// Counts describe saved measurements, never readiness or remote arm state.
class CueStatusSummary extends StatelessWidget {
  const CueStatusSummary({
    super.key,
    required this.readings,
    this.alignment = WrapAlignment.start,
    this.notInScript,
  });
  final List<CueReading> readings;
  final WrapAlignment alignment;
  final int? notInScript;

  @override
  Widget build(BuildContext context) {
    final counts = <String, int>{};
    for (final reading in readings) {
      final label = reading.label == 'Unknown' ? 'Unmeasured' : reading.label;
      counts.update(label, (count) => count + 1, ifAbsent: () => 1);
    }
    if (notInScript != null) counts['Not in script'] = notInScript!;
    return Wrap(
      alignment: alignment,
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final (label, color) in [
          ('Connected', Colors.lightGreen),
          ('Open', Colors.amber),
          ('Short', Colors.redAccent),
          ('Not in script', Colors.orange),
          ('Invalid', Colors.redAccent),
          ('Raw', Colors.amber),
          ('Unmeasured', Colors.blueGrey),
        ])
          if ((counts[label] ?? 0) > 0 ||
              (label == 'Not in script' && notInScript != null))
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                color: color.withValues(alpha: 0.08),
                border: Border.all(color: color.withValues(alpha: 0.45)),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(
                '${counts[label]} $label',
                style: TextStyle(color: color, fontSize: 14),
              ),
            ),
      ],
    );
  }
}
