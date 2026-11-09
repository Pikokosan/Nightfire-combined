import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../protocol/nfcp_codec.dart';
import 'cue_tile.dart';
import 'cue_status_summary.dart';
import 'module_battery.dart';

/// Read-only view of the existing module snapshots, with no radio access.
class GroupCueOverview extends StatefulWidget {
  const GroupCueOverview({
    super.key,
    required this.modules,
    required this.radioReady,
    this.onOpenModule,
  });

  final List<FieldModule> modules;
  final bool radioReady;
  final ValueChanged<ModuleFocus>? onOpenModule;

  @override
  State<GroupCueOverview> createState() => _GroupCueOverviewState();
}

class _GroupCueOverviewState extends State<GroupCueOverview>
    with SingleTickerProviderStateMixin {
  int? _group;
  (int, int)? _selectedCue, _lastPreview;
  late final _blink = AnimationController(
    vsync: this,
    duration: const Duration(seconds: 3),
  );
  late final _opacity = TweenSequence<double>([
    for (var i = 0; i < 6; i++)
      TweenSequenceItem(
        tween: Tween(begin: i.isEven ? 1.0 : 0.35, end: i.isEven ? 0.35 : 1.0),
        weight: 1,
      ),
  ]).animate(_blink);

  @override
  void dispose() {
    _blink.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final groups = widget.modules.map((module) => module.group).toSet().toList()
      ..sort();
    // Preserve a user's choice when modules update. If that group disappears,
    // require another choice rather than silently showing a different group.
    final selected = _group ?? (groups.isEmpty ? null : groups.first);
    final available = groups.contains(selected);
    final visible =
        widget.modules.where((module) => module.group == selected).toList()
          ..sort((a, b) => a.uid.compareTo(b.uid));
    final canPreview = visible.any(
      (module) =>
          module.uid == _selectedCue?.$1 &&
          _selectedCue!.$2 > 0 &&
          _selectedCue!.$2 <= module.cueCount,
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (groups.isNotEmpty || _group != null)
          DropdownButtonFormField<int>(
            key: ValueKey('overview-group-$selected-$available'),
            initialValue: available ? selected : null,
            isExpanded: true,
            decoration: const InputDecoration(
              labelText: 'Group',
              border: OutlineInputBorder(),
            ),
            hint: const Text('Select a group'),
            items: [
              for (final group in groups)
                DropdownMenuItem(value: group, child: Text('Group $group')),
            ],
            onChanged: groups.isEmpty
                ? null
                : (value) => setState(() => _group = value),
          ),
        if (groups.isEmpty)
          const Text(
            'No modules discovered. Open the module view to discover modules and check continuity.',
          )
        else if (!available)
          Text('Group $selected is no longer available. Select another group.'),
        if (widget.onOpenModule != null)
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: () => widget.onOpenModule!(
                ModuleFocus(group: available ? selected : null),
              ),
              icon: const Icon(Icons.grid_view_outlined),
              label: const Text('Open module view'),
            ),
          ),
        if (!widget.radioReady)
          const Text(
            'Radio disconnected • showing saved continuity.',
            style: TextStyle(color: Colors.amber),
          ),
        for (final module in visible) _module(context, module),
        if (visible.isNotEmpty) ...[
          const SizedBox(height: 12),
          Text(
            canPreview
                ? 'Selected for preview: Module ${_id(_selectedCue!.$1)} • Cue ${_selectedCue!.$2}'
                : 'Select a cue to preview its marker.',
          ),
          Align(
            alignment: Alignment.centerLeft,
            child: OutlinedButton.icon(
              onPressed: !canPreview
                  ? null
                  : () {
                      setState(() => _lastPreview = _selectedCue);
                      if (MediaQuery.disableAnimationsOf(context)) {
                        _blink.value = 1;
                      } else {
                        _blink.forward(from: 0);
                      }
                    },
              icon: const Icon(Icons.visibility_outlined),
              label: const Text('Preview cue'),
            ),
          ),
        ],
        if (_lastPreview case final last?)
          Text(
            'Last preview: Module ${_id(last.$1)} • Cue ${last.$2} • no command sent',
          ),
      ],
    );
  }

  String _id(int uid) =>
      '0x${uid.toRadixString(16).padLeft(8, '0').toUpperCase()}';

  Widget _cue(FieldModule module, int index, CueReading reading) {
    final cue = (module.uid, index + 1);
    return Semantics(
      button: true,
      selected: _selectedCue == cue,
      label:
          'Preview selection. Module ${module.id}, cue ${index + 1}${_lastPreview == cue ? '. Last previewed cue' : ''}',
      child: InkWell(
        key: ValueKey('preview-select-${module.uid}-$index'),
        borderRadius: BorderRadius.circular(10),
        onTap: () => setState(() => _selectedCue = cue),
        child: FadeTransition(
          key: ValueKey('preview-pulse-${module.uid}-$index'),
          opacity: _lastPreview == cue
              ? _opacity
              : const AlwaysStoppedAnimation(1.0),
          child: SizedBox(
            width: CueTile.tileWidth,
            height: CueTile.tileHeight,
            child: Stack(
              children: [
                CueTile(
                  key: ValueKey('overview-cue-${module.uid}-$index'),
                  number: index + 1,
                  reading: reading,
                ),
                if (_selectedCue == cue || _lastPreview == cue)
                  Positioned.fill(
                    child: IgnorePointer(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          border: Border.all(
                            color: Theme.of(context).colorScheme.onSurface,
                            width: 2,
                          ),
                          borderRadius: BorderRadius.circular(10),
                        ),
                      ),
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _module(BuildContext context, FieldModule module) {
    final snapshot = module.resistance;
    final readings = List.generate(
      module.cueCount,
      (index) => snapshot != null && index < snapshot.cues.length
          ? snapshot.cues[index]
          : const CueReading('Unknown', 'Not measured'),
    );
    return Padding(
      padding: const EdgeInsets.only(top: 16, bottom: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 12,
            runSpacing: 8,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(
                'Module ${module.id}',
                style: Theme.of(context).textTheme.titleMedium,
              ),
              ModuleBatteryForInfo(info: module.info),
            ],
          ),
          Text(
            '${module.cueCount} cues • Group ${module.group} • ${module.seenInScan ? 'Seen this scan' : 'Not seen in latest scan'}',
          ),
          const SizedBox(height: 8),
          CueStatusSummary(readings: readings),
          const SizedBox(height: 8),
          if (snapshot == null)
            const Text('Continuity not measured.')
          else ...[
            Text(
              'Measured: ${module.measuredAt?.toLocal().toString().split('.').first ?? 'Time unavailable'} • saved snapshot',
            ),
            if (snapshot.continuityOnly)
              const Text('Continuity only: resistance readings unavailable.'),
            if (!snapshot.calibrated && !snapshot.continuityOnly)
              const Text(
                'Raw ADC readings: calibration is unavailable.',
                style: TextStyle(color: Colors.amber),
              ),
            if (snapshot.status != 0)
              Text(
                'Measurement warning: ${snapshot.warning}',
                style: const TextStyle(color: Colors.amber),
              ),
          ],
          if (module.error != null)
            Text(module.error!, style: const TextStyle(color: Colors.amber)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (var index = 0; index < readings.length; index++)
                _cue(module, index, readings[index]),
            ],
          ),
        ],
      ),
    );
  }
}
