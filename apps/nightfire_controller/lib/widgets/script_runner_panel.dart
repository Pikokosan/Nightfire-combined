import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import '../scripts/script_document.dart';

/// Runs a loaded script: send it to the modules, arm, then start, pause,
/// resume and stop it, with disarm and STOP ALL always at hand.
class ScriptRunnerPanel extends StatefulWidget {
  const ScriptRunnerPanel({
    super.key,
    required this.session,
    required this.script,
  });
  final RadioSession? session;
  final ScriptDocument? script;

  @override
  State<ScriptRunnerPanel> createState() => _ScriptRunnerPanelState();
}

String formatScriptTime(int ms) {
  final negative = ms < 0;
  final total = ms.abs();
  final minutes = total ~/ 60000;
  final seconds = (total % 60000) / 1000;
  return '${negative ? '-' : ''}$minutes:${seconds.toStringAsFixed(1).padLeft(4, '0')}';
}

class _ScriptRunnerPanelState extends State<ScriptRunnerPanel> {
  Future<void> _run(Future<dynamic> Function() action) async {
    try {
      await action();
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('$error')));
      }
    }
  }

  Future<void> _startAtOffset(MasterController master) async {
    final controller = TextEditingController(text: '0');
    final text = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Start at offset'),
        content: TextField(
          controller: controller,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(labelText: 'Seconds into script'),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('Start'),
          ),
        ],
      ),
    );
    final seconds = double.tryParse(text?.trim() ?? '');
    if (seconds == null || seconds < 0) return;
    await master.startScript(offsetMs: (seconds * 1000).round());
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final script = widget.script;
    final master = session?.master;
    final theme = Theme.of(context);
    final supported = master?.supported == true;
    final busy = session?.busy == true;
    final sent =
        master != null && script != null && identical(master.document, script);
    final scriptUids = master?.uploaded.keys.toSet() ?? const <int>{};
    final allArmed =
        sent &&
        scriptUids.isNotEmpty &&
        scriptUids.every(master.armed.contains);
    final running = master?.running == true;
    final paused = master?.paused == true;
    final stepping = master?.stepping == true;
    final active = running || paused || stepping;
    final interrupted = master?.interrupted == true;
    final scriptErrors =
        script != null &&
        (script.findings.any((f) => f.error) ||
            script.events.isEmpty ||
            script.auxiliaryCount != 0);
    final duration = script == null || script.events.isEmpty
        ? 0
        : script.events.map((e) => e.timeMs).reduce((a, b) => a > b ? a : b);
    final clock = running
        ? master!.elapsedMs
        : paused
        ? master!.pausedAtMs!
        : 0;
    final next = script?.events
        .where((e) => e.timeMs >= clock)
        .fold<ScriptEvent?>(
          null,
          (best, e) => best == null || e.timeMs < best.timeMs ? e : best,
        );

    // The single next thing the operator has to do, shown when Start is off.
    final String? blocked = master == null || !supported
        ? 'Connect a radio with usb-master-0.4 firmware in Radio.'
        : script == null
        ? 'Load a script to begin.'
        : scriptErrors
        ? 'Fix the script errors below before sending it.'
        : master.stopping
        ? 'Sending STOP…'
        : interrupted
        ? 'Stopped. Press Disarm to confirm every module is safe.'
        : !sent
        ? 'Send the script to the modules.'
        : !allArmed
        ? 'Arm the script modules.'
        : null;

    final canSend =
        supported &&
        !busy &&
        !interrupted &&
        script != null &&
        !scriptErrors &&
        master!.armed.isEmpty &&
        !active;
    final canStart =
        blocked == null && !busy && !running && !stepping && !master!.stopping;
    final canPause = running && !busy;
    final canStop = active && !busy && !master!.stopping;
    final canArm =
        supported &&
        !busy &&
        !interrupted &&
        sent &&
        !allArmed &&
        !running &&
        !stepping;
    final canDisarm = supported && !busy && !master!.stopping;

    final (stateText, stateColor) = !supported
        ? ('OFFLINE', theme.colorScheme.outline)
        : running
        ? ('RUNNING', Colors.lightGreen)
        : paused
        ? ('PAUSED', Colors.amber)
        : stepping
        ? ('STEP MODE', Colors.amber)
        : master!.completed && sent
        ? ('COMPLETE', Colors.lightBlue)
        : interrupted
        ? ('STOPPED', Colors.redAccent)
        : allArmed
        ? ('ARMED', Colors.orange)
        : sent
        ? ('READY', theme.colorScheme.primary)
        : ('NOT SENT', theme.colorScheme.outline);

    Widget big(
      String label,
      IconData icon,
      VoidCallback? onPressed, {
      Color? color,
      bool filled = true,
    }) {
      final style = filled
          ? FilledButton.styleFrom(
              backgroundColor: color,
              foregroundColor: color == null ? null : Colors.white,
              minimumSize: const Size(140, 56),
            )
          : OutlinedButton.styleFrom(minimumSize: const Size(140, 56));
      return filled
          ? FilledButton.icon(
              onPressed: onPressed,
              style: style,
              icon: Icon(icon),
              label: Text(label),
            )
          : OutlinedButton.icon(
              onPressed: onPressed,
              style: style,
              icon: Icon(icon),
              label: Text(label),
            );
    }

    return Card(
      key: const Key('script-runner'),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              spacing: 12,
              runSpacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text('Script runner', style: theme.textTheme.titleLarge),
                Container(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 10,
                    vertical: 4,
                  ),
                  decoration: BoxDecoration(
                    border: Border.all(color: stateColor),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    stateText,
                    style: TextStyle(
                      color: stateColor,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              script == null
                  ? 'No script loaded'
                  : '${script.name} • ${script.events.length} events • ${formatScriptTime(duration)}',
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.end,
              children: [
                Text(
                  formatScriptTime(clock),
                  key: const Key('script-clock'),
                  style: theme.textTheme.displaySmall?.copyWith(
                    fontFeatures: const [FontFeature.tabularFigures()],
                  ),
                ),
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text('/ ${formatScriptTime(duration)}'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            LinearProgressIndicator(
              value: duration == 0 ? 0 : (clock / duration).clamp(0.0, 1.0),
            ),
            const SizedBox(height: 8),
            Text(
              next == null
                  ? (script == null ? '' : 'No events left')
                  : 'Next: ${formatScriptTime(next.timeMs)} • group ${next.channel} cue ${next.cue}'
                        '${next.description.isEmpty ? '' : ' • ${next.description}'}',
            ),
            if (paused && master!.skippedOnPause > 0)
              Text(
                '${master.skippedOnPause} event(s) were due at the pause and will be skipped on resume.',
                style: const TextStyle(color: Colors.amber),
              ),
            const SizedBox(height: 16),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                big(
                  'Send to modules',
                  Icons.upload,
                  canSend
                      ? () => _run(() => master.uploadScript(script))
                      : null,
                  filled: false,
                ),
                big(
                  paused ? 'Resume' : 'Start',
                  Icons.play_arrow,
                  canStart
                      ? () => _run(
                          () => master.startScript(
                            offsetMs: master.pausedAtMs ?? 0,
                          ),
                        )
                      : null,
                  color: Colors.green.shade700,
                ),
                big(
                  'Pause',
                  Icons.pause,
                  canPause ? () => _run(master!.pauseScript) : null,
                  color: Colors.amber.shade800,
                ),
                big(
                  'Stop',
                  Icons.stop,
                  canStop ? () => _run(master.stopScript) : null,
                  color: Colors.blueGrey.shade600,
                ),
              ],
            ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 12,
              runSpacing: 12,
              children: [
                big(
                  'Arm',
                  Icons.lock_open,
                  canArm ? () => _run(master.armScriptModules) : null,
                  color: Colors.orange.shade800,
                ),
                big(
                  'Disarm',
                  Icons.lock,
                  canDisarm ? () => _run(master.disarmAll) : null,
                  filled: false,
                ),
                big(
                  'STOP ALL & DISARM',
                  Icons.stop_circle,
                  master != null && supported
                      ? () => _run(master.stopAllAndDisarm)
                      : null,
                  color: Colors.red.shade800,
                ),
              ],
            ),
            if (busy) ...[
              const SizedBox(height: 12),
              const LinearProgressIndicator(),
            ],
            const SizedBox(height: 12),
            if (blocked != null && !active)
              Text(blocked, style: const TextStyle(color: Colors.amber)),
            if (master != null && supported) Text(master.status),
            if (master != null && supported)
              Text(
                master.armSummary,
                style: const TextStyle(color: Colors.amber),
              ),
            if (master != null && supported)
              ExpansionTile(
                key: const PageStorageKey('script-runner-advanced'),
                tilePadding: EdgeInsets.zero,
                title: const Text('Advanced'),
                expandedCrossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      OutlinedButton(
                        onPressed: canStart && !paused
                            ? () => _run(() => _startAtOffset(master))
                            : null,
                        child: const Text('Start at offset'),
                      ),
                      OutlinedButton(
                        onPressed: canStart && !paused
                            ? () => _run(() => master.startScript(step: true))
                            : null,
                        child: const Text('Step mode'),
                      ),
                      OutlinedButton.icon(
                        onPressed:
                            stepping &&
                                !busy &&
                                master.stepIndex < master.eventTimes.length
                            ? () => _run(master.nextStep)
                            : null,
                        icon: const Icon(Icons.skip_next),
                        label: const Text('FIRE next event'),
                      ),
                      OutlinedButton(
                        onPressed:
                            supported &&
                                !busy &&
                                master.armed.isEmpty &&
                                !active &&
                                master.uploaded.isNotEmpty
                            ? () => _run(master.clearScript)
                            : null,
                        child: const Text('Clear script from modules'),
                      ),
                    ],
                  ),
                  if (stepping)
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(
                        'Next step: ${master.stepIndex + 1}/${master.eventTimes.length}',
                      ),
                    ),
                  const SizedBox(height: 8),
                ],
              ),
          ],
        ),
      ),
    );
  }
}
