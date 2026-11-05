import 'package:flutter/material.dart';

import '../protocol/nfcp_codec.dart';

class CueTile extends StatelessWidget {
  const CueTile({
    super.key,
    required this.number,
    required this.reading,
    this.outsideScript = false,
    this.focused = false,
  });
  static const double tileWidth = 145;
  static const double tileHeight = 104;
  final int number;
  final CueReading reading;
  final bool outsideScript, focused;
  bool get unused => outsideScript && reading.label == 'Connected';

  Color get color => unused
      ? Colors.orange
      : switch (reading.label) {
          'Connected' => Colors.lightGreen,
          'Short' || 'Invalid' => Colors.redAccent,
          'Unknown' => Colors.blueGrey,
          _ => Colors.amber,
        };

  @override
  Widget build(BuildContext context) {
    final title = 'Cue $number • ${reading.label}';
    final detail = '${reading.detail}${unused ? ' • Not in script' : ''}';
    return Tooltip(
      message: '$title\n$detail',
      excludeFromSemantics: true,
      child: Semantics(
        label:
            '$title. $detail${focused ? '. Selected from script check' : ''}',
        excludeSemantics: true,
        child: Container(
          width: tileWidth,
          height: tileHeight,
          padding: const EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: focused || unused ? color.withValues(alpha: 0.10) : null,
            border: Border.all(color: color, width: focused ? 2 : 1),
            borderRadius: BorderRadius.circular(10),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Flexible(
                child: Text(
                  title,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: color),
                ),
              ),
              const SizedBox(height: 4),
              Flexible(
                child: Text(
                  detail,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
