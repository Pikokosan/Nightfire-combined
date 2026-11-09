import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';
import '../settings/battery_settings.dart';
import 'module_battery.dart';

/// App settings. Battery settings are kept per firmware target so field and
/// dummy modules can use different packs and divider calibration.
class SettingsPanel extends StatelessWidget {
  const SettingsPanel({super.key, required this.session});
  final RadioSession? session;

  @override
  Widget build(BuildContext context) {
    final settings = BatterySettingsScope.of(context);
    final modules = session?.modules.values.toList() ?? const <FieldModule>[];
    final targets = <String>{
      ...kKnownTargets.keys,
      ...settings.customisedTargets.where((t) => t != 'unknown'),
      for (final module in modules)
        if ((module.info['App'] ?? '').trim().isNotEmpty)
          module.info['App']!.trim(),
    };
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Modules', style: Theme.of(context).textTheme.titleLarge),
        SwitchListTile(
          key: const ValueKey('auto-continuity'),
          contentPadding: EdgeInsets.zero,
          title: const Text('Automatic continuity checks'),
          subtitle: const Text(
            'After discovery, check one module each heartbeat while nothing '
            'is armed. Its light flashes green when the reply arrives.',
          ),
          value: session?.autoContinuity ?? true,
          onChanged: session == null
              ? null
              : (enabled) => session!.autoContinuity = enabled,
        ),
        const SizedBox(height: 24),
        Text('Battery', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 4),
        const Text(
          'Pack type sets the empty and full voltages for the battery gauge '
          '(3.2–4.2 V per cell). Calibration corrects the displayed voltage so it '
          'matches a meter. These settings only change what the app shows; '
          'module firmware is not changed.',
        ),
        const SizedBox(height: 12),
        for (final target in targets)
          _TargetCard(
            key: ValueKey('battery-$target'),
            target: target,
            session: session,
            modules: [
              for (final module in modules)
                if ((module.info['App'] ?? '').trim() == target) module,
            ],
          ),
      ],
    );
  }
}

class _TargetCard extends StatelessWidget {
  const _TargetCard({
    super.key,
    required this.target,
    required this.session,
    required this.modules,
  });
  final String target;
  final RadioSession? session;
  final List<FieldModule> modules;

  @override
  Widget build(BuildContext context) {
    final settings = BatterySettingsScope.of(context);
    final profile = settings.profileFor(target);
    final theme = Theme.of(context);
    final withReading = [
      for (final m in modules)
        if (settings.read(m.info).reportedMv != null) m,
    ];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 12,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                  kKnownTargets[target] ?? target,
                  style: theme.textTheme.titleMedium,
                ),
                Text(
                  'Firmware target: $target',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
            const SizedBox(height: 12),
            ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 360),
              child: DropdownButtonFormField<String?>(
                key: ValueKey('pack-$target-${profile.packType}'),
                initialValue: profile.packType,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Pack type'),
                items: [
                  const DropdownMenuItem<String?>(
                    value: null,
                    child: Text('As reported by module'),
                  ),
                  for (final pack in kPackTypes)
                    DropdownMenuItem<String?>(
                      value: pack,
                      child: Text(
                        '$pack • ${(cellCount(pack)! * kEmptyCellMv / 1000).toStringAsFixed(1)}–'
                        '${(cellCount(pack)! * kFullCellMv / 1000).toStringAsFixed(1)} V',
                      ),
                    ),
                ],
                onChanged: (value) => settings.setProfile(
                  target,
                  profile.copyWith(packType: () => value),
                ),
              ),
            ),
            const SizedBox(height: 12),
            Text(
              profile.calibrated
                  ? 'Voltage correction: ×${profile.scale.toStringAsFixed(4)}'
                        '${profile.offsetMv == 0 ? '' : ' ${profile.offsetMv > 0 ? '+' : '−'} ${profile.offsetMv.abs()} mV'}'
                  : 'Voltage correction: none (showing the module reading)',
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.tonalIcon(
                  key: ValueKey('calibrate-$target'),
                  onPressed: withReading.isEmpty
                      ? null
                      : () =>
                            _calibrate(context, settings, profile, withReading),
                  icon: const Icon(Icons.speed),
                  label: const Text('Calibrate with meter'),
                ),
                OutlinedButton(
                  onPressed: () => _manual(context, settings, profile),
                  child: const Text('Enter correction'),
                ),
                TextButton(
                  onPressed: profile.calibrated
                      ? () => settings.setProfile(
                          target,
                          profile.copyWith(scale: 1.0, offsetMv: 0),
                        )
                      : null,
                  child: const Text('Clear calibration'),
                ),
              ],
            ),
            if (withReading.isEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  session?.ready == true
                      ? 'No $target module with a battery reading. Discover modules in Modules first.'
                      : 'Connect the radio and discover modules to calibrate with a meter.',
                  style: theme.textTheme.bodySmall,
                ),
              ),
            for (final module in modules) ...[
              const SizedBox(height: 8),
              _ModuleRow(module: module),
            ],
          ],
        ),
      ),
    );
  }

  Future<void> _calibrate(
    BuildContext context,
    BatterySettings settings,
    BatteryProfile profile,
    List<FieldModule> candidates,
  ) async {
    final result = await showDialog<double>(
      context: context,
      builder: (context) =>
          _CalibrationDialog(session: session, candidates: candidates),
    );
    if (result == null) return;
    settings.setProfile(target, profile.copyWith(scale: result, offsetMv: 0));
  }

  Future<void> _manual(
    BuildContext context,
    BatterySettings settings,
    BatteryProfile profile,
  ) async {
    final scale = TextEditingController(text: profile.scale.toString());
    final offset = TextEditingController(text: profile.offsetMv.toString());
    final saved = await showDialog<BatteryProfile>(
      context: context,
      builder: (context) {
        String? error;
        return StatefulBuilder(
          builder: (context, setState) => AlertDialog(
            title: const Text('Voltage correction'),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text('Shown voltage = module reading × scale + offset'),
                TextField(
                  controller: scale,
                  keyboardType: const TextInputType.numberWithOptions(
                    decimal: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Scale'),
                ),
                TextField(
                  controller: offset,
                  keyboardType: const TextInputType.numberWithOptions(
                    signed: true,
                  ),
                  decoration: const InputDecoration(labelText: 'Offset (mV)'),
                ),
                if (error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text(
                      error!,
                      style: const TextStyle(color: Colors.amber),
                    ),
                  ),
              ],
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: () {
                  final s = double.tryParse(scale.text.trim());
                  final o = int.tryParse(offset.text.trim());
                  if (s == null || s <= 0.5 || s >= 2) {
                    setState(() => error = 'Scale must be between 0.5 and 2.');
                  } else if (o == null || o.abs() > 5000) {
                    setState(
                      () => error =
                          'Offset must be a whole number within ±5000 mV.',
                    );
                  } else {
                    Navigator.pop(
                      context,
                      profile.copyWith(scale: s, offsetMv: o),
                    );
                  }
                },
                child: const Text('Save'),
              ),
            ],
          ),
        );
      },
    );
    if (saved != null) settings.setProfile(target, saved);
  }
}

class _ModuleRow extends StatelessWidget {
  const _ModuleRow({required this.module});
  final FieldModule module;

  @override
  Widget build(BuildContext context) {
    final reading = BatterySettingsScope.of(context).read(module.info);
    return Wrap(
      spacing: 12,
      crossAxisAlignment: WrapCrossAlignment.center,
      children: [
        Text('Module ${module.id}'),
        ModuleBatteryForInfo(info: module.info),
        Text(
          reading.reportedMv == null
              ? 'no reading'
              : 'module reports ${(reading.reportedMv! / 1000).toStringAsFixed(2)} V'
                    ' • pack ${module.info['Battery type']?.trim().isNotEmpty == true ? module.info['Battery type'] : 'not reported'}',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ],
    );
  }
}

/// Picks a module, refreshes its reading, and asks for the meter voltage.
/// Returns the new scale.
class _CalibrationDialog extends StatefulWidget {
  const _CalibrationDialog({required this.session, required this.candidates});
  final RadioSession? session;
  final List<FieldModule> candidates;

  @override
  State<_CalibrationDialog> createState() => _CalibrationDialogState();
}

class _CalibrationDialogState extends State<_CalibrationDialog> {
  late FieldModule _module = widget.candidates.first;
  final _meter = TextEditingController();
  String? _error;
  bool _reading = false;

  int? get _reported =>
      BatterySettingsScope.of(context).read(_module.info).reportedMv;

  Future<void> _refresh() async {
    final session = widget.session;
    if (session == null) return;
    setState(() {
      _reading = true;
      _error = null;
    });
    try {
      await session.readModule(_module.uid);
    } catch (error) {
      _error = 'Could not read module: $error';
    }
    if (mounted) setState(() => _reading = false);
  }

  void _save() {
    final volts = double.tryParse(_meter.text.trim().replaceAll(',', '.'));
    final reported = _reported;
    if (volts == null || reported == null) {
      setState(() => _error = 'Enter the voltage your meter shows, in volts.');
      return;
    }
    try {
      Navigator.pop(
        context,
        BatterySettings.calibrationScale(reported, (volts * 1000).round()),
      );
    } on FormatException catch (error) {
      setState(() => _error = error.message);
    }
  }

  @override
  void dispose() {
    _meter.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final reported = _reported;
    final session = widget.session;
    return AlertDialog(
      title: const Text('Calibrate with meter'),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (widget.candidates.length > 1)
              DropdownButtonFormField<FieldModule>(
                initialValue: _module,
                decoration: const InputDecoration(labelText: 'Module'),
                items: [
                  for (final m in widget.candidates)
                    DropdownMenuItem(value: m, child: Text('Module ${m.id}')),
                ],
                onChanged: (m) => setState(() => _module = m ?? _module),
              )
            else
              Text('Module ${_module.id}'),
            const SizedBox(height: 12),
            Text(
              reported == null
                  ? 'Module reading: unavailable'
                  : 'Module reading: ${(reported / 1000).toStringAsFixed(3)} V',
            ),
            TextButton.icon(
              onPressed:
                  _reading ||
                      session == null ||
                      session.busy ||
                      !session.canQueryModules
                  ? null
                  : _refresh,
              icon: const Icon(Icons.refresh),
              label: Text(_reading ? 'Reading…' : 'Read again'),
            ),
            const Text(
              'Measure the pack at the module with your meter at the same time, '
              'then enter what the meter shows. Read again first if the pack has '
              'changed since the last reading.',
            ),
            TextField(
              key: const Key('meter-volts'),
              controller: _meter,
              autofocus: true,
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
              ),
              decoration: const InputDecoration(labelText: 'Meter reading (V)'),
              onSubmitted: (_) => _save(),
            ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  _error!,
                  style: const TextStyle(color: Colors.amber),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          key: const Key('save-calibration'),
          onPressed: _reading ? null : _save,
          child: const Text('Save'),
        ),
      ],
    );
  }
}
