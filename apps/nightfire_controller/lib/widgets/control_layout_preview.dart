import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import 'group_cue_overview.dart';
import 'offline_control_simulation.dart';

/// Visual controls only. This widget has no transport or command callbacks.
class ControlLayoutPreview extends StatefulWidget {
  const ControlLayoutPreview({
    super.key,
    this.modules = const [],
    this.radioReady = false,
    this.onOpenModule,
  });

  final List<FieldModule> modules;
  final bool radioReady;
  final ValueChanged<ModuleFocus>? onOpenModule;

  @override
  State<ControlLayoutPreview> createState() => _ControlLayoutPreviewState();
}

class _ControlLayoutPreviewState extends State<ControlLayoutPreview> {
  bool _manual = false;

  @override
  Widget build(BuildContext context) => Card(
    margin: const EdgeInsets.symmetric(vertical: 12),
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Control layout preview',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 8),
          SegmentedButton<bool>(
            segments: const [
              ButtonSegment(value: false, label: Text('Script')),
              ButtonSegment(value: true, label: Text('Manual fire')),
            ],
            selected: {_manual},
            onSelectionChanged: (selection) =>
                setState(() => _manual = selection.single),
          ),
          const SizedBox(height: 12),
          if (_manual) ...[
            GroupCueOverview(
              modules: widget.modules,
              radioReady: widget.radioReady,
              onOpenModule: widget.onOpenModule,
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: null,
              icon: const Icon(Icons.touch_app_outlined),
              label: const Text('Fire'),
            ),
          ] else
            Wrap(
              spacing: 12,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('Start'),
                ),
                OutlinedButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.pause),
                  label: const Text('Pause'),
                ),
                OutlinedButton.icon(
                  onPressed: null,
                  icon: const Icon(Icons.stop),
                  label: const Text('Stop'),
                ),
              ],
            ),
          const SizedBox(height: 8),
          Text(
            'Layout only • controls are disabled and send no commands.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
          TextButton.icon(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => const OfflineControlSimulation(),
            ),
            icon: const Icon(Icons.science_outlined),
            label: const Text('Open offline simulation'),
          ),
        ],
      ),
    ),
  );
}
