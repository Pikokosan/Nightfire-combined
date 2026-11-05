import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../scripts/script_document.dart';

/// A local preview. Selecting a time never sends a radio command.
class ScriptTimeline extends StatefulWidget {
  const ScriptTimeline({super.key, required this.events, this.onOpenModule});

  final List<CheckedScriptEvent> events;
  final ValueChanged<ModuleFocus>? onOpenModule;

  @override
  State<ScriptTimeline> createState() => _ScriptTimelineState();
}

class _ScriptTimelineState extends State<ScriptTimeline> {
  int _index = 0;
  int _page = 0;

  void _select(int index) => setState(() {
    _index = index;
    _page = 0;
  });

  static String _time(int ms) {
    final hours = ms ~/ 3600000;
    final minutes = (ms ~/ 60000 % 60).toString().padLeft(2, '0');
    final seconds = (ms ~/ 1000 % 60).toString().padLeft(2, '0');
    final fraction = (ms % 1000).toString().padLeft(3, '0');
    return '${hours.toString().padLeft(2, '0')}:$minutes:$seconds.$fraction';
  }

  @override
  Widget build(BuildContext context) {
    final groups = <int, List<CheckedScriptEvent>>{};
    for (final row in widget.events) {
      groups.putIfAbsent(row.event.timeMs, () => []).add(row);
    }
    final times = groups.keys.toList()..sort();
    if (times.isEmpty) return const Text('No valid events to preview.');
    final index = _index.clamp(0, times.length - 1);
    final selected = times[index];
    final rows = groups[selected]!;
    final page = _page * 25 < rows.length ? _page : 0;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text(
          'Preview only • select a scheduled time to inspect its cues.',
        ),
        Text(
          'Last event: ${_time(times.last)} • ${times.length} scheduled times',
        ),
        Row(
          children: [
            IconButton(
              tooltip: 'Previous scheduled time',
              onPressed: index == 0 ? null : () => _select(index - 1),
              icon: const Icon(Icons.skip_previous),
            ),
            Expanded(
              child: Slider(
                value: selected.toDouble(),
                max: times.last == 0 ? 1 : times.last.toDouble(),
                label: _time(selected),
                semanticFormatterCallback: (_) => _time(selected),
                onChanged: times.length == 1
                    ? null
                    : (value) {
                        // Binary search keeps scrubbing responsive for large files.
                        var low = 0;
                        var high = times.length;
                        while (low < high) {
                          final middle = (low + high) ~/ 2;
                          if (times[middle] < value) {
                            low = middle + 1;
                          } else {
                            high = middle;
                          }
                        }
                        var nearest = low.clamp(0, times.length - 1);
                        if (nearest > 0 &&
                            value - times[nearest - 1] <
                                times[nearest] - value) {
                          nearest--;
                        }
                        _select(nearest);
                      },
              ),
            ),
            IconButton(
              tooltip: 'Next scheduled time',
              onPressed: index == times.length - 1
                  ? null
                  : () => _select(index + 1),
              icon: const Icon(Icons.skip_next),
            ),
          ],
        ),
        Text('${_time(selected)} • ${rows.length} cue events'),
        if (rows.length > 25)
          Row(
            children: [
              IconButton(
                tooltip: 'Previous preview cues',
                onPressed: page == 0
                    ? null
                    : () => setState(() => _page = page - 1),
                icon: const Icon(Icons.chevron_left),
              ),
              Text(
                '${page * 25 + 1}–${(page * 25 + 25).clamp(0, rows.length)} of ${rows.length}',
              ),
              IconButton(
                tooltip: 'Next preview cues',
                onPressed: (page + 1) * 25 >= rows.length
                    ? null
                    : () => setState(() => _page = page + 1),
                icon: const Icon(Icons.chevron_right),
              ),
            ],
          ),
        for (final row in rows.skip(page * 25).take(25))
          ListTile(
            title: Text('Group ${row.event.channel} • Cue ${row.event.cue}'),
            subtitle: Text('${row.event.description}\n${row.status}'),
            trailing: widget.onOpenModule == null
                ? null
                : const Icon(Icons.chevron_right),
            onTap: widget.onOpenModule == null
                ? null
                : () => widget.onOpenModule!(
                    ModuleFocus(group: row.event.channel, cue: row.event.cue),
                  ),
          ),
      ],
    );
  }
}
