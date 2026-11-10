import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';
import '../protocol/nfcp_codec.dart';
import 'cue_tile.dart';

/// Manual fire screen modeled on master_main.py's Manual Fire view:
/// step through modules (CHANNEL -/+), tap a cue to fire it immediately.
/// Every fire goes through [MasterController.fire], which requires a
/// confirmed per-module ARM and verifies the field output pulse.
class ManualFirePanel extends StatefulWidget {
  const ManualFirePanel({super.key, required this.session});
  final RadioSession session;

  @override
  State<ManualFirePanel> createState() => _ManualFirePanelState();
}

class _ManualFirePanelState extends State<ManualFirePanel> {
  int _index = 0;

  Future<void> _run(Future<dynamic> Function() action) async {
    try {
      await action();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  List<FieldModule> get _modules =>
      widget.session.modules.values.where((m) => m.seenInScan).toList()
        ..sort((a, b) => a.group.compareTo(b.group));

  void _step(int delta, int count) =>
      setState(() => _index = (_index + delta) % count);

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final master = session.master;
    final theme = Theme.of(context);

    if (!master.supported) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text(
            'Manual fire requires a connected radio running usb-master-0.4 bridge firmware.',
          ),
        ),
      );
    }

    final modules = _modules;
    final idle = master.available && !master.running && !master.stepping;
    final module = modules.isEmpty
        ? null
        : modules[_index.clamp(0, modules.length - 1)];
    final moduleArmed = module != null && master.armed.contains(module.uid);
    final canFire = idle && moduleArmed;
    final last = master.lastFire;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('Manual fire', style: theme.textTheme.titleLarge),
            const SizedBox(height: 8),
            Text(master.status),
            Text(
              master.armSummary,
              style: const TextStyle(color: Colors.amber),
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: idle && master.canArm && modules.isNotEmpty
                      ? () => _run(master.armAll)
                      : null,
                  icon: const Icon(Icons.lock_open),
                  label: const Text('ARM ALL'),
                ),
                OutlinedButton.icon(
                  onPressed: !session.busy && modules.isNotEmpty
                      ? () => _run(master.disarmAll)
                      : null,
                  icon: const Icon(Icons.lock),
                  label: const Text('DISARM ALL'),
                ),
                FilledButton.icon(
                  onPressed: () => _run(master.emergencyStop),
                  style: FilledButton.styleFrom(
                    backgroundColor: Colors.red.shade800,
                    foregroundColor: Colors.white,
                  ),
                  icon: const Icon(Icons.stop_circle),
                  label: const Text('STOP / DISARM ALL'),
                ),
              ],
            ),
            if (session.busy) ...[
              const SizedBox(height: 8),
              const LinearProgressIndicator(),
            ],
            const SizedBox(height: 16),
            if (module == null)
              const Text(
                'No discovered modules. Discover modules on the Modules tab first.',
              )
            else ...[
              Row(
                children: [
                  IconButton.filledTonal(
                    tooltip: 'Previous module (CHANNEL -)',
                    onPressed: modules.length > 1
                        ? () => _step(-1, modules.length)
                        : null,
                    icon: const Icon(Icons.chevron_left),
                  ),
                  Expanded(
                    child: Column(
                      children: [
                        Text('CHANNEL', style: theme.textTheme.labelMedium),
                        Text(
                          '${module.group}',
                          style: theme.textTheme.displaySmall,
                        ),
                        Text(
                          '${module.id} • ${module.cueCount} cues • '
                          '${moduleArmed ? "ARMED" : "not armed"}',
                          textAlign: TextAlign.center,
                          style: TextStyle(
                            color: moduleArmed ? Colors.amber : null,
                          ),
                        ),
                      ],
                    ),
                  ),
                  IconButton.filledTonal(
                    tooltip: 'Next module (CHANNEL +)',
                    onPressed: modules.length > 1
                        ? () => _step(1, modules.length)
                        : null,
                    icon: const Icon(Icons.chevron_right),
                  ),
                ],
              ),
              const SizedBox(height: 8),
              Center(
                child: Wrap(
                  spacing: 8,
                  children: [
                    TextButton.icon(
                      onPressed: idle && master.canArm && !moduleArmed
                          ? () => _run(() => master.armModule(module.uid))
                          : null,
                      icon: const Icon(Icons.lock_open, size: 18),
                      label: const Text('Arm this module'),
                    ),
                    TextButton.icon(
                      onPressed: !session.busy && moduleArmed
                          ? () => _run(() => master.disarmModule(module.uid))
                          : null,
                      icon: const Icon(Icons.lock, size: 18),
                      label: const Text('Disarm this module'),
                    ),
                  ],
                ),
              ),
              if (!moduleArmed)
                const Padding(
                  padding: EdgeInsets.only(bottom: 8),
                  child: Text(
                    'Manual fire disabled - not armed',
                    style: TextStyle(color: Colors.amber),
                  ),
                ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (var cue = 1; cue <= module.cueCount; cue++)
                    _CueFireButton(
                      key: ValueKey('fire-cue-${module.uid}-$cue'),
                      cue: cue,
                      reading:
                          module.resistance?.cues.elementAtOrNull(cue - 1) ??
                          const CueReading('Unknown', 'Not measured'),
                      lastFired: last?.uid == module.uid && last?.cue == cue,
                      fired:
                          master.firedCues[module.uid]?.contains(cue) ?? false,
                      onPressed: canFire
                          ? () => _run(() => master.fire(module.uid, cue))
                          : null,
                    ),
                ],
              ),
              if (last != null) ...[
                const SizedBox(height: 12),
                Text(
                  'Last sent: ${session.modules[last.uid]?.id ?? "module"} '
                  'cue ${last.cue}',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ],
          ],
        ),
      ),
    );
  }
}

/// The module dashboard's [CueTile], made tappable for firing.
class _CueFireButton extends StatelessWidget {
  const _CueFireButton({
    super.key,
    required this.cue,
    required this.reading,
    required this.lastFired,
    required this.fired,
    required this.onPressed,
  });
  final int cue;
  final CueReading reading;

  /// Most recent cue sent: outlined.
  final bool lastFired;

  /// Sent since the module was armed: flame stays until disarm.
  final bool fired;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) => Semantics(
    button: true,
    enabled: onPressed != null,
    label:
        'Fire cue $cue${fired ? '. Fired' : ''}${lastFired ? '. Last fired' : ''}',
    excludeSemantics: true,
    child: Opacity(
      opacity: onPressed == null ? 0.45 : 1,
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onPressed,
          child: SizedBox(
            width: CueTile.tileWidth,
            height: CueTile.tileHeight,
            child: Stack(
              children: [
                CueTile(number: cue, reading: reading, focused: lastFired),
                if (fired || lastFired)
                  const Positioned(
                    right: 8,
                    bottom: 8,
                    child: Icon(
                      Icons.local_fire_department,
                      color: Colors.deepOrange,
                    ),
                  ),
                if (lastFired)
                  Positioned.fill(
                    child: IgnorePointer(
                      child: DecoratedBox(
                        decoration: BoxDecoration(
                          border: Border.all(
                            color: Colors.deepOrange,
                            width: 3,
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
    ),
  );
}
