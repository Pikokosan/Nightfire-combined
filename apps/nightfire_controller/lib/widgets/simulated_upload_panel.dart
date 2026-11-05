import 'dart:async';

import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../protocol/nfcp_codec.dart';
import '../scripts/script_document.dart';

enum DemoUploadOutcome { success, timeout, rejected }

class _DemoTarget {
  _DemoTarget(this.group, this.module, this.events);
  final int group, events;
  final String module;
  String status = 'Queued';
  String? error;
  int progress = 0;
}

/// A timer-driven UI demo. No transport, protocol encoding, or device writes.
class SimulatedUploadPanel extends StatefulWidget {
  const SimulatedUploadPanel({
    super.key,
    required this.script,
    required this.modules,
    required this.radioReady,
  });
  final ScriptDocument script;
  final List<FieldModule> modules;
  final bool radioReady;

  @override
  State<SimulatedUploadPanel> createState() => _SimulatedUploadPanelState();
}

class _SimulatedUploadPanelState extends State<SimulatedUploadPanel> {
  bool _demoModules = true;
  DemoUploadOutcome _outcome = DemoUploadOutcome.success;
  List<_DemoTarget> _rows = [];
  Timer? _timer;
  bool _running = false;
  String _summary = 'No simulation run yet.';

  List<FieldModule> _modules() {
    if (!_demoModules) return widget.modules;
    final counts = <int, int>{};
    for (final event in widget.script.events) {
      counts.update(
        event.channel,
        (value) => value > event.cue ? value : event.cue,
        ifAbsent: () => event.cue,
      );
    }
    return [
      for (final group in counts.keys)
        FieldModule(group, group, counts[group]!, 0)
          ..seenInScan = true
          ..measuredAt = DateTime.now().subtract(const Duration(seconds: 1))
          ..resistance = ResistanceSnapshot(
            0,
            true,
            List.filled(
              counts[group]!,
              const CueReading('Connected', 'Demo reading'),
            ),
          ),
    ];
  }

  void _start(ScriptReport report, {bool retry = false}) {
    if (_running || report.errors != 0 || report.events.isEmpty) return;
    if (!retry) {
      final groups =
          widget.script.events.map((event) => event.channel).toSet().toList()
            ..sort();
      _rows = [
        for (final group in groups)
          _DemoTarget(
            group,
            _demoModules
                ? 'Demo module, group $group'
                : report.events
                      .firstWhere((row) => row.event.channel == group)
                      .moduleId!,
            widget.script.events
                .where((event) => event.channel == group)
                .length,
          ),
      ];
    }
    final targets = _rows
        .where((row) => !retry || row.status == 'Failed (simulated)')
        .toList();
    if (targets.isEmpty) return;
    for (final row in targets) {
      row.status = 'Queued';
      row.error = null;
      row.progress = 0;
    }
    var index = 0;
    final outcome = _outcome;
    setState(() {
      _running = true;
      _summary = 'Simulating ${targets.length} module results…';
    });
    _timer = Timer.periodic(const Duration(milliseconds: 350), (timer) {
      setState(() {
        final row = targets[index];
        row.status = 'Simulating';
        row.progress++;
        if (row.progress == 3) {
          row.status = index == 0 && outcome != DemoUploadOutcome.success
              ? 'Failed (simulated)'
              : 'Complete (simulated)';
          if (row.status == 'Failed (simulated)') {
            row.error = outcome == DemoUploadOutcome.timeout
                ? 'Demo timeout: no response simulated.'
                : 'Demo rejection: target refused in simulation.';
          }
          index++;
          if (index == targets.length) {
            timer.cancel();
            _running = false;
            final failures = _rows
                .where((row) => row.status == 'Failed (simulated)')
                .length;
            _summary = '$failures simulated failures • no hardware changed.';
          }
        }
      });
    });
  }

  void _cancel() {
    _timer?.cancel();
    setState(() {
      for (final row in _rows) {
        if (row.status == 'Queued' || row.status == 'Simulating') {
          row.status = 'Cancelled';
        }
      }
      _running = false;
      _summary = 'Simulation cancelled • no hardware changed.';
    });
  }

  void _reset() {
    _rows = [];
    _summary = 'No simulation run yet.';
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final report = ScriptReport.check(
      widget.script,
      _modules(),
      radioReady: _demoModules || widget.radioReady,
    );
    final valid = report.errors == 0 && report.events.isNotEmpty;
    return ExpansionTile(
      key: const PageStorageKey('simulated-upload'),
      title: const Text('Simulated upload'),
      subtitle: const Text('Local demo • never writes to modules'),
      childrenPadding: const EdgeInsets.all(16),
      expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SwitchListTile(
          contentPadding: EdgeInsets.zero,
          title: const Text('Use demo modules'),
          subtitle: Text(
            _demoModules
                ? 'Invented matching modules with connected demo readings.'
                : 'Validate against discovered snapshots; transfer remains simulated.',
          ),
          value: _demoModules,
          onChanged: _running
              ? null
              : (value) => setState(() {
                  _demoModules = value;
                  _reset();
                }),
        ),
        Text(
          '${report.errors} validation errors • ${report.warnings} warnings',
        ),
        if (!valid)
          Text(
            report.findings
                .where((finding) => finding.error)
                .take(3)
                .map((finding) => finding.text)
                .join('\n'),
          ),
        DropdownButtonFormField<DemoUploadOutcome>(
          initialValue: _outcome,
          decoration: const InputDecoration(labelText: 'Demo result'),
          items: const [
            DropdownMenuItem(
              value: DemoUploadOutcome.success,
              child: Text('All succeed'),
            ),
            DropdownMenuItem(
              value: DemoUploadOutcome.timeout,
              child: Text('First target times out'),
            ),
            DropdownMenuItem(
              value: DemoUploadOutcome.rejected,
              child: Text('First target rejects'),
            ),
          ],
          onChanged: _running
              ? null
              : (value) {
                  if (value != null) setState(() => _outcome = value);
                },
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: [
            OutlinedButton(
              onPressed: _running || !valid ? null : () => _start(report),
              child: const Text('Simulate upload'),
            ),
            TextButton(
              onPressed:
                  !_running &&
                      valid &&
                      _rows.any((row) => row.status == 'Failed (simulated)')
                  ? () => _start(report, retry: true)
                  : null,
              child: const Text('Retry failed simulation'),
            ),
            TextButton(
              onPressed: _running ? _cancel : null,
              child: const Text('Cancel simulation'),
            ),
          ],
        ),
        Text(_summary),
        const Text(
          'Results describe the snapshot captured at start. They do not change module script IDs or Loaded status.',
        ),
        for (final row in _rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${row.module} • Group ${row.group} • ${row.events} cue events',
                ),
                LinearProgressIndicator(value: row.progress / 3),
                Text(row.status),
                if (row.error != null) Text(row.error!),
                if (row.status == 'Failed (simulated)')
                  const Text(
                    'Injected demo error. Choose All succeed, then retry the failed simulation.',
                  ),
              ],
            ),
          ),
      ],
    );
  }
}
