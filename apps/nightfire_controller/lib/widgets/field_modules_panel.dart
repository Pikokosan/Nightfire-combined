import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import '../controller/field_module.dart';
import '../scripts/script_document.dart';
import '../protocol/nfcp_codec.dart';
import 'cue_tile.dart';
import 'cue_status_summary.dart';
import 'module_battery.dart';
import 'maintenance_tools.dart';
import 'module_settings.dart';

class FieldModulesPanel extends StatefulWidget {
  const FieldModulesPanel({
    super.key,
    required this.session,
    this.script,
    this.focus,
  });
  final RadioSession session;
  final ScriptDocument? script;
  final ModuleFocus? focus;

  @override
  State<FieldModulesPanel> createState() => _FieldModulesPanelState();
}

class _FieldModulesPanelState extends State<FieldModulesPanel> {
  String _search = '';
  String _cueFilter = 'All cues';
  final _searchController = TextEditingController();
  final _targetHeader = GlobalKey();
  ModuleFocus? _focus;
  int _focusRevision = 0;
  final _settingsTabs = <int, bool>{};
  final _detailsRevision = <int, int>{};
  RadioSession get session => widget.session;

  int? _notInScript(Iterable<FieldModule> modules) {
    final script = widget.script;
    if (script == null) return null;
    var count = 0;
    for (final module in modules) {
      for (var index = 0; index < module.cueCount; index++) {
        if (_reading(module.uid, index).label == 'Connected' &&
            !script.usesCue(module.group, index + 1)) {
          count++;
        }
      }
    }
    return count;
  }

  @override
  void initState() {
    super.initState();
    _focus = widget.focus;
    if (_focus != null) _revealFocus();
  }

  @override
  void didUpdateWidget(covariant FieldModulesPanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.focus, oldWidget.focus)) {
      _focus = widget.focus;
      _focusRevision++;
      _search = '';
      _searchController.clear();
      _cueFilter = 'All cues';
      for (final module in session.modules.values) {
        if (_focus == null || _focus!.matches(module)) {
          _settingsTabs[module.uid] = false;
          _detailsRevision[module.uid] =
              (_detailsRevision[module.uid] ?? 0) + 1;
        }
      }
      if (_focus != null) _revealFocus();
    }
  }

  void _revealFocus() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || _focus == null) return;
      final target = _targetHeader.currentContext;
      if (target != null) {
        Scrollable.ensureVisible(
          target,
          alignment: 0.05,
          duration: const Duration(milliseconds: 250),
        );
      }
    });
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Widget _header(FieldModule module, bool target) => LayoutBuilder(
    key: target ? _targetHeader : null,
    builder: (context, constraints) {
      final details = Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Wrap(
            spacing: 12,
            runSpacing: 4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text('Module ${module.id}'),
              ModuleBatteryForInfo(info: module.info),
              if (module.rssi case final rssi?) SignalStrength(rssi: rssi),
            ],
          ),
          const SizedBox(height: 4),
          Text(
            '${module.cueCount} cues • Group ${module.group} • Script: ${module.scriptStatus} • ${session.ready ? (module.seenInScan ? 'Seen this scan' : 'Not seen in latest scan') : 'Radio disconnected'}',
            style: Theme.of(context).textTheme.bodyMedium,
          ),
        ],
      );
      final summary = Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          ReplyLed(
            key: ValueKey('reply-led-${module.uid}'),
            flash: module.replyFlash,
            failed: module.noReply,
          ),
          const SizedBox(width: 8),
          Flexible(
            child: CueStatusSummary(
              notInScript: _notInScript([module]),
              alignment: WrapAlignment.end,
              readings: [
                for (var index = 0; index < module.cueCount; index++)
                  _reading(module.uid, index),
              ],
            ),
          ),
        ],
      );
      if (constraints.maxWidth >= 620) {
        return Row(
          children: [
            Expanded(child: details),
            const SizedBox(width: 12),
            Flexible(child: summary),
          ],
        );
      }
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          details,
          const SizedBox(height: 8),
          Align(alignment: Alignment.centerRight, child: summary),
        ],
      );
    },
  );

  static const filters = [
    'All cues',
    'Connected',
    'Open',
    'Short / invalid',
    'Raw',
    'Unmeasured',
  ];

  CueReading _reading(int uid, int index) =>
      session.modules[uid]!.resistance?.cues[index] ??
      const CueReading('Unknown', 'Not measured');

  bool _matches(CueReading reading) => switch (_cueFilter) {
    'All cues' => true,
    'Short / invalid' => reading.label == 'Short' || reading.label == 'Invalid',
    'Unmeasured' => reading.label == 'Unknown',
    _ => reading.label == _cueFilter,
  };

  @override
  Widget build(BuildContext context) {
    final modules = session.modules.values.toList()
      ..sort((first, second) => first.uid.compareTo(second.uid));
    final visibleModules = modules
        .where(
          (module) =>
              (_focus == null || _focus!.matches(module)) &&
              '${module.id} ${module.info['App'] ?? ''} group ${module.group}'
                  .toLowerCase()
                  .contains(_search),
        )
        .toList();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const SizedBox(height: 24),
        Text('Field modules', style: Theme.of(context).textTheme.headlineSmall),
        const SizedBox(height: 4),
        Text(
          '${modules.length} module${modules.length == 1 ? '' : 's'} • ${modules.fold<int>(0, (total, module) => total + module.cueCount)} cues',
        ),
        const SizedBox(height: 12),
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: session.busy || !session.canQueryModules
                  ? null
                  : session.discoverModules,
              icon: const Icon(Icons.radar),
              label: const Text('Discover modules'),
            ),
            OutlinedButton.icon(
              onPressed:
                  session.busy || !session.canQueryModules || modules.isEmpty
                  ? null
                  : session.checkAllContinuity,
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text('Check all continuity'),
            ),
          ],
        ),
        if (session.continuityProgress case final progress?) ...[
          const SizedBox(height: 12),
          const LinearProgressIndicator(),
          Text(progress),
        ],
        const SizedBox(height: 12),
        if (session.ready && !session.canQueryModules)
          const Text(
            'Update the radio to USB monitor 0.2 or newer for module diagnostics.',
          ),
        if (modules.isEmpty)
          Text(
            _focus?.group == null
                ? 'Run discovery to find powered field modules on the selected network and channel.'
                : 'Script group ${_focus!.group} is not currently discovered. Run discovery to check its module.',
          ),
        if (modules.isNotEmpty) ...[
          if (widget.script case final script?) ...[
            Text('Script: ${script.name}'),
            const Text(
              'Orange cues: connected but not in the loaded script.',
              style: TextStyle(color: Colors.orange),
            ),
            const SizedBox(height: 8),
          ],
          if (_focus != null) ...[
            Wrap(
              spacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                  'Script check: ${_focus!.group == null ? 'Modules' : 'Group ${_focus!.group}'}${_focus!.cue == null ? '' : ' • Cue ${_focus!.cue}'}',
                ),
                TextButton(
                  onPressed: () => setState(() => _focus = null),
                  child: const Text('Show all modules'),
                ),
              ],
            ),
          ],
          CueStatusSummary(
            notInScript: _notInScript(modules),
            readings: [
              for (final module in modules)
                for (var index = 0; index < module.cueCount; index++)
                  _reading(module.uid, index),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            session.ready
                ? 'Counts reflect saved measurement snapshots across all modules.'
                : 'Radio disconnected • saved readings are not current.',
          ),
          const SizedBox(height: 16),
          TextField(
            key: const ValueKey('module-search'),
            controller: _searchController,
            decoration: const InputDecoration(
              labelText: 'Find a module',
              hintText: 'Module ID, app or group',
              prefixIcon: Icon(Icons.search),
              border: OutlineInputBorder(),
            ),
            onChanged: (value) =>
                setState(() => _search = value.trim().toLowerCase()),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (final filter in filters)
                ChoiceChip(
                  label: Text(filter),
                  selected: _cueFilter == filter,
                  onSelected: (_) => setState(() => _cueFilter = filter),
                ),
            ],
          ),
          const SizedBox(height: 12),
          Text(
            'Showing ${visibleModules.length} of ${modules.length} module${modules.length == 1 ? '' : 's'} • $_cueFilter',
          ),
          if (visibleModules.isEmpty)
            Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Text(
                _focus == null ? 'No modules match your search.' : 'The module referenced by this script finding is not currently discovered.',
              ),
            ),
        ],
        for (final module in visibleModules)
          Card(
            key: ValueKey('module-card-${module.uid}'),
            child: ExpansionTile(
              key: PageStorageKey('module-${module.uid}-$_focusRevision'),
              // Collapsed until opened, except the module a script finding
              // points at.
              initiallyExpanded:
                  _focus != null && module == visibleModules.first,
              onExpansionChanged: (expanded) {
                if (expanded) {
                  setState(() {
                    _settingsTabs[module.uid] = false;
                    _detailsRevision[module.uid] =
                        (_detailsRevision[module.uid] ?? 0) + 1;
                  });
                }
              },
              expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
              expandedAlignment: Alignment.topLeft,
              title: _header(
                module,
                _focus != null && module == visibleModules.first,
              ),
              children: [
                // The tile saves its expanded state (a bool) in PageStorage
                // under its own key path. Without a key of their own, the
                // scrollables inside (the group field's editor) would share
                // that path and read the bool back as a scroll offset.
                Padding(
                  key: PageStorageKey('module-body-${module.uid}'),
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      SegmentedButton<bool>(
                        segments: const [
                          ButtonSegment(
                            value: false,
                            icon: Icon(Icons.grid_view_outlined),
                            label: Text('Status'),
                          ),
                          ButtonSegment(
                            value: true,
                            icon: Icon(Icons.settings_outlined),
                            label: Text('Settings'),
                          ),
                        ],
                        selected: {_settingsTabs[module.uid] ?? false},
                        onSelectionChanged: (selection) => setState(() {
                          _settingsTabs[module.uid] = selection.single;
                          if (!selection.single) {
                            _detailsRevision[module.uid] =
                                (_detailsRevision[module.uid] ?? 0) + 1;
                          }
                        }),
                      ),
                      const SizedBox(height: 16),
                      if (_settingsTabs[module.uid] == true) ...[
                        ModuleSettings(
                          key: ValueKey('settings-${module.uid}'),
                          module: module,
                          session: session,
                        ),
                        const Divider(height: 32),
                        ModuleMaintenance(
                          key: ValueKey('maintenance-${module.uid}'),
                          session: session,
                          module: module,
                        ),
                      ] else ...[
                        Wrap(
                          spacing: 12,
                          runSpacing: 8,
                          children: [
                            OutlinedButton(
                              onPressed:
                                  session.busy || !session.canQueryModules
                                  ? null
                                  : () => session.readModule(
                                      module.uid,
                                      resistance: true,
                                    ),
                              child: const Text('Check continuity'),
                            ),
                          ],
                        ),
                        if (module.error != null)
                          Text(
                            module.error!,
                            style: const TextStyle(color: Colors.amber),
                          ),
                        if (module.resistance case final snapshot?) ...[
                          const SizedBox(height: 12),
                          Text(
                            'Measured: ${module.measuredAt?.toLocal().toString().split('.').first ?? 'Time unavailable'} • snapshot',
                          ),
                          if (snapshot.continuityOnly)
                            const Text(
                              'Continuity only: this module did not return resistance readings.',
                            ),
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
                          const SizedBox(height: 8),
                        ] else
                          const Padding(
                            padding: EdgeInsets.only(top: 8),
                            child: Text('Continuity not measured.'),
                          ),
                        const SizedBox(height: 8),
                        Wrap(
                          spacing: 8,
                          runSpacing: 8,
                          children: [
                            for (
                              var index = 0;
                              index < module.cueCount;
                              index++
                            )
                              if (_matches(_reading(module.uid, index)))
                                CueTile(
                                  key: ValueKey('cue-${module.uid}-$index'),
                                  number: index + 1,
                                  reading: _reading(module.uid, index),
                                  outsideScript:
                                      widget.script != null &&
                                      !widget.script!.usesCue(
                                        module.group,
                                        index + 1,
                                      ),
                                  focused:
                                      _focus?.cue == index + 1 &&
                                      _focus!.matches(module),
                                ),
                          ],
                        ),
                        if (!List.generate(
                          module.cueCount,
                          (index) => _matches(_reading(module.uid, index)),
                        ).contains(true))
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 16),
                            child: Text('No cues match this filter.'),
                          ),
                        const SizedBox(height: 12),
                        ExpansionTile(
                          key: PageStorageKey(
                            'module-details-${module.uid}-${_detailsRevision[module.uid] ?? 0}',
                          ),
                          initiallyExpanded: false,
                          tilePadding: EdgeInsets.zero,
                          title: const Text('Module details'),
                          children: [
                            if (module.lastSeen != null)
                              Text(
                                'Last reply: ${module.lastSeen!.toLocal().toString().split('.').first}',
                              ),
                            Text(
                              'RSSI: ${module.rssi ?? '—'} dBm • Device type 0x${module.deviceType.toRadixString(16)} • Protocol 0x${module.protocolVersion.toRadixString(16)}',
                            ),
                            if (module.routeText != null)
                              Text(
                                'Route: ${module.routeText}',
                                key: ValueKey('module-route-${module.uid}'),
                              ),
                            for (final entry in module.info.entries)
                              if (entry.value.isNotEmpty &&
                                  entry.key != 'Battery mV')
                                Text('${entry.key}: ${entry.value}'),
                            TextButton.icon(
                              onPressed:
                                  session.busy || !session.canQueryModules
                                  ? null
                                  : () => session.readModule(module.uid),
                              icon: const Icon(Icons.refresh),
                              label: const Text('Read info'),
                            ),
                          ],
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }
}

/// A small status LED that lights green when [flash] changes (an
/// automatic continuity reply arrived) and fades back to dark. It stays red
/// while [failed] (the last check got no reply).
class ReplyLed extends StatefulWidget {
  const ReplyLed({super.key, required this.flash, this.failed = false});
  final int flash;
  final bool failed;

  @override
  State<ReplyLed> createState() => _ReplyLedState();
}

class _ReplyLedState extends State<ReplyLed>
    with SingleTickerProviderStateMixin {
  static const lit = Color(0xFF4CE65A);
  static const red = Color(0xFFFF4D4D);
  late final _fade = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1500),
    value: 1,
  );

  @override
  void didUpdateWidget(ReplyLed old) {
    super.didUpdateWidget(old);
    if (widget.flash != old.flash) _fade.forward(from: 0);
  }

  @override
  void dispose() {
    _fade.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: _fade,
    builder: (context, _) {
      final dark = Theme.of(context).colorScheme.outlineVariant;
      final failed = widget.failed;
      final glow = failed ? 1.0 : 1 - Curves.easeIn.transform(_fade.value);
      final on = failed ? red : lit;
      return Container(
        width: 10,
        height: 10,
        decoration: BoxDecoration(
          shape: BoxShape.circle,
          color: Color.lerp(dark, on, glow),
          boxShadow: [
            if (glow > 0)
              BoxShadow(
                color: on.withValues(alpha: 0.6 * glow),
                blurRadius: 6,
                spreadRadius: 1,
              ),
          ],
        ),
      );
    },
  );
}

/// The module's last received signal strength, next to its battery.
class SignalStrength extends StatelessWidget {
  const SignalStrength({super.key, required this.rssi});
  final int rssi;

  @override
  Widget build(BuildContext context) {
    // nRF52840 at 1 Mbps: about -94 dBm sensitivity. Leave a fade margin.
    final color = rssi >= -70
        ? Colors.lightGreen
        : rssi >= -85
        ? Colors.amber
        : Colors.redAccent;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(Icons.signal_cellular_alt, size: 18, color: color),
        const SizedBox(width: 4),
        Text('$rssi dBm', style: TextStyle(color: color)),
      ],
    );
  }
}
