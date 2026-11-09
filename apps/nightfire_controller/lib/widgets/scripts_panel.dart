import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import '../controller/field_module.dart';
import '../scripts/script_document.dart';
import 'script_timeline.dart';
import 'simulated_upload_panel.dart';
import 'script_runner_panel.dart';

class ScriptSource {
  const ScriptSource(this.filename, this.text);
  final String filename, text;
}

class ScriptsPanel extends StatefulWidget {
  const ScriptsPanel({
    super.key,
    required this.session,
    this.pickScript,
    this.onOpenRadio,
    this.onScriptChanged,
    this.onOpenModule,
    this.onPickingScriptChanged,
  });
  final RadioSession? session;
  final Future<ScriptSource?> Function()? pickScript;
  final VoidCallback? onOpenRadio;
  final ValueChanged<ScriptDocument?>? onScriptChanged;
  final ValueChanged<ModuleFocus>? onOpenModule;
  final ValueChanged<bool>? onPickingScriptChanged;

  @override
  State<ScriptsPanel> createState() => _ScriptsPanelState();
}

class _ScriptsPanelState extends State<ScriptsPanel> {
  ScriptDocument? _script;
  String? _loadError;
  bool _loading = false;
  bool _checking = false;
  int _eventPage = 0;
  int _findingPage = 0;

  Future<ScriptSource?> _pick() async {
    const type = XTypeGroup(
      label: 'COBRA CSV scripts',
      extensions: ['csv', 'txt'],
      mimeTypes: ['text/csv', 'text/plain', 'application/vnd.ms-excel'],
      uniformTypeIdentifiers: [
        'public.comma-separated-values-text',
        'public.plain-text',
      ],
    );
    final file = await openFile(acceptedTypeGroups: [type]);
    if (file == null) return null;
    if (await file.length() > ScriptDocument.maxBytes) {
      throw const FormatException('Script exceeds the 1 MiB limit.');
    }
    return ScriptSource(file.name, utf8.decode(await file.readAsBytes()));
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _loadError = null;
    });
    try {
      ScriptSource? source;
      widget.onPickingScriptChanged?.call(true);
      try {
        source = await (widget.pickScript ?? _pick)();
      } finally {
        widget.onPickingScriptChanged?.call(false);
      }
      if (!mounted || source == null) return;
      // Replace the whole document, including invalid imports; never retain an
      // earlier successful report under the name of a newly selected file.
      setState(() {
        _script = null;
        _eventPage = 0;
        _findingPage = 0;
      });
      widget.onScriptChanged?.call(null);
      final document = ScriptDocument.parse(source.filename, source.text);
      if (mounted) {
        setState(() => _script = document);
        widget.onScriptChanged?.call(document);
      }
    } catch (error) {
      if (mounted) {
        widget.onScriptChanged?.call(null);
        setState(() {
          _script = null;
          _loadError = '$error';
        });
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _check() async {
    final session = widget.session;
    if (session == null || session.busy || !session.canQueryModules) return;
    setState(() => _checking = true);
    try {
      await session.discoverModules();
      if (!mounted ||
          !identical(session, widget.session) ||
          !session.canQueryModules) {
        return;
      }
      await session.checkAllContinuity();
    } finally {
      if (mounted) setState(() => _checking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final script = _script;
    final master = session?.master;
    // Swapping scripts mid-run would orphan the running one: Stop it first.
    final busy =
        _loading ||
        _checking ||
        session?.busy == true ||
        master?.running == true ||
        master?.paused == true ||
        master?.stepping == true;
    final report = script == null
        ? null
        : ScriptReport.check(
            script,
            session?.modules.values ?? [],
            radioReady: session?.ready == true,
          );
    final events = report?.events ?? [];
    final findings = report?.findings ?? [];
    final findingPage = _findingPage * 50 < findings.length ? _findingPage : 0;
    final start = _eventPage * 25;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Script workspace',
          style: Theme.of(context).textTheme.headlineLarge,
        ),
        const SizedBox(height: 8),
        const Text(
          'Review scheduled cues and check them against your modules.',
        ),
        const SizedBox(height: 16),
        Wrap(
          spacing: 12,
          runSpacing: 8,
          children: [
            FilledButton.icon(
              onPressed: busy ? null : _load,
              icon: const Icon(Icons.file_open_outlined),
              label: const Text('Load script'),
            ),
            OutlinedButton.icon(
              onPressed:
                  busy || script == null || session?.canQueryModules != true
                  ? null
                  : _check,
              icon: const Icon(Icons.fact_check_outlined),
              label: const Text('Check connected modules'),
            ),
            if (script != null)
              TextButton(
                onPressed: busy
                    ? null
                    : () {
                        setState(() {
                          _script = null;
                          _loadError = null;
                          _eventPage = 0;
                          _findingPage = 0;
                        });
                        widget.onScriptChanged?.call(null);
                      },
                child: const Text('Unload'),
              ),
          ],
        ),
        const SizedBox(height: 12),
        if (session?.ready != true)
          Card(
            margin: EdgeInsets.zero,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Wrap(
                spacing: 16,
                runSpacing: 8,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  const Text('Radio offline • reconnect to check hardware.'),
                  if (widget.onOpenRadio != null)
                    TextButton.icon(
                      onPressed: widget.onOpenRadio,
                      icon: const Icon(Icons.usb),
                      label: const Text('Open radio connection'),
                    ),
                ],
              ),
            ),
          ),
        const SizedBox(height: 12),
        ScriptRunnerPanel(session: session, script: script),
        ExpansionTile(
          key: const PageStorageKey('script-help'),
          tilePadding: EdgeInsets.zero,
          leading: const Icon(Icons.help_outline),
          title: const Text('How script checks work'),
          expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
          childrenPadding: const EdgeInsets.only(bottom: 12),
          children: const [
            Text(
              'Script channel = module group. Cues are numbered from 1. Saved continuity readings remain until refreshed; use Check connected modules to take new readings.',
            ),
            SizedBox(height: 8),
            Text(
              'Load a CSV, send it to the modules, arm, then Start. Pause stops every module and resumes after the last cue that fired. STOP ALL & DISARM ends the run and disarms everything.',
              style: TextStyle(color: Colors.amber),
            ),
          ],
        ),
        if (_loading || _checking) ...[
          const SizedBox(height: 12),
          const LinearProgressIndicator(),
          Text(
            _loading
                ? 'Opening script…'
                : (session?.continuityProgress ??
                      session?.status ??
                      'Checking modules…'),
          ),
        ],
        if (_loadError != null)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              'Import failed: $_loadError',
              style: const TextStyle(color: Colors.redAccent),
            ),
          ),
        if (script == null && _loadError == null && !_loading)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 24),
            child: Text(
              'No script loaded. Choose a CSV file to view its events and hardware checks.',
            ),
          ),
        if (script != null && report != null) ...[
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    script.name,
                    style: Theme.of(context).textTheme.titleLarge,
                  ),
                  Text(
                    script.filename,
                    style: Theme.of(context).textTheme.bodySmall?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '${script.events.length} cue events • ${script.events.map((event) => event.channel).toSet().length} channels • ${script.auxiliaryCount} auxiliary rows',
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '${report.errors} errors • ${report.warnings} warnings',
                    style: TextStyle(
                      color: report.errors > 0
                          ? Colors.redAccent
                          : Colors.lightGreen,
                    ),
                  ),
                  if (report.errors == 0)
                    const Text(
                      'Referenced cues have connected snapshots. This does not confirm arm state or execution readiness.',
                    ),
                ],
              ),
            ),
          ),
          SimulatedUploadPanel(
            key: ObjectKey(script),
            script: script,
            modules: session?.modules.values.toList() ?? const [],
            radioReady: session?.ready == true,
          ),
          ExpansionTile(
            key: PageStorageKey('script-timeline-${script.filename}'),
            title: const Text('Timeline preview'),
            childrenPadding: const EdgeInsets.all(16),
            expandedCrossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              ScriptTimeline(
                key: ObjectKey(script),
                events: events,
                onOpenModule: widget.onOpenModule,
              ),
            ],
          ),
          if (report.findings.isNotEmpty)
            ExpansionTile(
              key: PageStorageKey('script-findings-${script.filename}'),
              initiallyExpanded: true,
              title: Text('Check results (${report.findings.length})'),
              children: [
                if (findings.length > 50)
                  Wrap(
                    crossAxisAlignment: WrapCrossAlignment.center,
                    children: [
                      IconButton(
                        tooltip: 'Previous findings',
                        onPressed: findingPage == 0
                            ? null
                            : () => setState(
                                () => _findingPage = findingPage - 1,
                              ),
                        icon: const Icon(Icons.chevron_left),
                      ),
                      Text(
                        '${findingPage * 50 + 1}–${(findingPage * 50 + 50).clamp(0, findings.length)} of ${findings.length}',
                      ),
                      IconButton(
                        tooltip: 'Next findings',
                        onPressed: (findingPage + 1) * 50 >= findings.length
                            ? null
                            : () => setState(
                                () => _findingPage = findingPage + 1,
                              ),
                        icon: const Icon(Icons.chevron_right),
                      ),
                    ],
                  ),
                for (final finding in findings.skip(findingPage * 50).take(50))
                  InkWell(
                    key: ValueKey('${finding.line}:${finding.message}'),
                    onTap: widget.onOpenModule == null
                        ? null
                        : () {
                            var focus = finding.focus;
                            if (focus.group == null && focus.uid == null) {
                              final events = script.events.where(
                                (event) => event.line == finding.line,
                              );
                              if (events.isNotEmpty) {
                                focus = ModuleFocus(
                                  group: events.first.channel,
                                  cue: events.first.cue,
                                );
                              }
                            }
                            widget.onOpenModule!(focus);
                          },
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 6,
                      ),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(
                            finding.error
                                ? Icons.error_outline
                                : Icons.warning_amber,
                            color: finding.error
                                ? Colors.redAccent
                                : Colors.amber,
                            size: 20,
                          ),
                          const SizedBox(width: 8),
                          Expanded(child: Text(finding.text)),
                          if (widget.onOpenModule != null)
                            const Padding(
                              padding: EdgeInsets.only(left: 8),
                              child: Icon(Icons.chevron_right, size: 20),
                            ),
                        ],
                      ),
                    ),
                  ),
              ],
            ),
          const SizedBox(height: 16),
          Text('Cue events', style: Theme.of(context).textTheme.titleLarge),
          Wrap(
            spacing: 12,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              IconButton(
                tooltip: 'Previous events',
                onPressed: _eventPage == 0
                    ? null
                    : () => setState(() => _eventPage--),
                icon: const Icon(Icons.chevron_left),
              ),
              Text(
                events.isEmpty
                    ? 'No valid events'
                    : '${start + 1}–${(start + 25).clamp(0, events.length)} of ${events.length}',
              ),
              IconButton(
                tooltip: 'Next events',
                onPressed: start + 25 >= events.length
                    ? null
                    : () => setState(() => _eventPage++),
                icon: const Icon(Icons.chevron_right),
              ),
            ],
          ),
          for (final row in events.skip(start).take(25))
            Card(
              child: ListTile(
                leading: Icon(
                  row.status == 'Connected'
                      ? Icons.check_circle_outline
                      : Icons.error_outline,
                  color: row.status == 'Connected'
                      ? Colors.lightGreen
                      : Colors.redAccent,
                ),
                title: Text(
                  '${row.event.time} • Channel ${row.event.channel} / Cue ${row.event.cue}',
                ),
                subtitle: Text(
                  'Line ${row.event.line}${row.moduleId == null ? '' : ' • ${row.moduleId}'}\n${row.event.description}\n${row.status}${row.measuredAt == null ? '' : '\nMeasured: ${row.measuredAt!.toLocal().toString().split('.').first} • saved snapshot'}',
                ),
              ),
            ),
        ],
      ],
    );
  }
}
