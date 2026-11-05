import 'dart:convert';

import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import '../protocol/nfcp_codec.dart';
import '../scripts/script_document.dart';

class MasterControlsPanel extends StatefulWidget {
  const MasterControlsPanel({
    super.key,
    required this.session,
    this.script,
    this.scriptsOnly = false,
    this.onPickingFile,
  });
  final RadioSession session;
  final ScriptDocument? script;
  final bool scriptsOnly;
  final ValueChanged<bool>? onPickingFile;
  @override
  State<MasterControlsPanel> createState() => _MasterControlsPanelState();
}

class _MasterControlsPanelState extends State<MasterControlsPanel> {
  int? _uid;
  int _cue = 1;

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

  Future<String?> _input(
    String title,
    String hint, {
    String initial = '',
  }) async {
    final controller = TextEditingController(text: initial);
    final result = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(title),
        content: TextField(
          controller: controller,
          decoration: InputDecoration(labelText: hint),
          maxLines: title.contains('Calibration') ? 8 : 1,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text),
            child: const Text('Apply'),
          ),
        ],
      ),
    );
    // Dialog route may retain its TextField until its reverse animation finishes.
    return result;
  }

  int _number(String text) => text.trim().toLowerCase().startsWith('0x')
      ? int.parse(text.trim().substring(2), radix: 16)
      : int.parse(text.trim());

  Future<void> _setting(int command, String name, String hint) async {
    final value = await _input(name, hint);
    if (value == null || !mounted || _uid == null) return;
    await widget.session.master.setModuleValue(_uid!, command, _number(value));
  }

  Future<void> _update({bool bridge = false}) async {
    widget.onPickingFile?.call(true);
    XFile? file;
    try {
      file = await openFile();
    } finally {
      widget.onPickingFile?.call(false);
    }
    if (file == null || !mounted) return;
    if (await file.length() > 256 * 1024) {
      throw const FormatException('Maximum file size is 256 KiB');
    }
    final bytes = await file.readAsBytes();
    final path = await _input(
      bridge ? 'Update bridge file' : 'Update module file',
      'Destination filename',
      initial: file.name,
    );
    if (path == null || !mounted) return;
    if (bridge) {
      await widget.session.master.updateBridge(path, bytes);
    } else if (_uid != null) {
      await widget.session.master.updateModule(_uid!, path, bytes);
    }
  }

  Future<void> _calibrate() async {
    final bank = await _input(
      'Calibration bank',
      'Bank index (0-based)',
      initial: '0',
    );
    if (bank == null || !mounted) return;
    final text = await _input(
      'Calibration points',
      'One ADC mV,deci-ohms pair per line',
    );
    if (text == null || !mounted || _uid == null) return;
    final points = const LineSplitter()
        .convert(text)
        .where((s) => s.trim().isNotEmpty)
        .map((line) {
          final values = line.split(',');
          if (values.length != 2) {
            throw const FormatException('Use ADC mV,deci-ohms');
          }
          return (_number(values[0]), _number(values[1]));
        })
        .toList();
    await widget.session.master.calibrate(_uid!, _number(bank), points);
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final master = session.master;
    final modules = session.modules.values.toList()
      ..sort((a, b) => a.group.compareTo(b.group));
    final selected = modules.where((m) => m.uid == _uid).firstOrNull;
    final output = selected?.outputStatus;
    final enabled = master.available;
    final maintenance =
        enabled && master.armed.isEmpty && !master.running && !master.stepping;
    Widget button(
      String text,
      Future<dynamic> Function()? action, {
      IconData? icon,
    }) => OutlinedButton.icon(
      onPressed: action == null ? null : () => _run(action),
      icon: Icon(icon ?? Icons.tune),
      label: Text(text),
    );
    if (!master.supported) {
      return const Card(
        child: Padding(
          padding: EdgeInsets.all(16),
          child: Text(
            'Live master controls require usb-master-0.4.0 bridge firmware. Monitor firmware supports diagnostics only.',
          ),
        ),
      );
    }
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.scriptsOnly
                  ? 'Live script controller'
                  : 'Master controller',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            Text(master.status),
            Text(
              master.armSummary,
              style: const TextStyle(color: Colors.amber),
            ),
            const SizedBox(height: 8),
            FilledButton.icon(
              onPressed: () => _run(master.emergencyStop),
              style: FilledButton.styleFrom(
                backgroundColor: Colors.red.shade800,
                foregroundColor: Colors.white,
              ),
              icon: const Icon(Icons.stop_circle),
              label: const Text('STOP / DISARM ALL'),
            ),
            if (session.busy) const LinearProgressIndicator(),
            if (!widget.scriptsOnly) ...[
              const SizedBox(height: 12),
              DropdownButtonFormField<int>(
                key: ValueKey(selected?.uid),
                initialValue: selected?.uid,
                isExpanded: true,
                decoration: const InputDecoration(labelText: 'Target module'),
                items: [
                  for (final module in modules)
                    DropdownMenuItem(
                      value: module.uid,
                      child: Text('Group ${module.group} • ${module.id}'),
                    ),
                ],
                onChanged: enabled || master.interrupted
                    ? (value) => setState(() {
                        _uid = value;
                        _cue = 1;
                      })
                    : null,
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  button(
                    'ARM selected module',
                    enabled &&
                            selected != null &&
                            !master.running &&
                            !master.stepping
                        ? () => master.armModule(selected.uid)
                        : null,
                    icon: Icons.lock_open,
                  ),
                  button(
                    'DISARM selected module',
                    !session.busy && selected != null
                        ? () => master.disarmModule(selected.uid)
                        : null,
                    icon: Icons.lock,
                  ),
                ],
              ),
              if (selected != null) ...[
                const SizedBox(height: 8),
                Text(
                  output == null
                      ? 'Physical output state: not read'
                      : 'Physical outputs: ${output.armed ? "ARMED" : "disarmed"} • '
                            '${output.driverEnabled ? "driver enabled" : "driver disabled"} • '
                            'active 0x${output.activeMask.toRadixString(16).toUpperCase()} • '
                            'last 0x${output.lastFireMask.toRadixString(16).toUpperCase()} '
                            '(pulse ${output.fireSequence})',
                  style: TextStyle(
                    color: output?.startupFault == true
                        ? Colors.red
                        : output?.armed == true
                        ? Colors.amber
                        : null,
                  ),
                ),
                TextButton.icon(
                  onPressed: enabled
                      ? () =>
                            _run(() => master.refreshOutputStatus(selected.uid))
                      : null,
                  icon: const Icon(Icons.sync),
                  label: const Text('Refresh physical output state'),
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<int>(
                  key: ValueKey('${selected.uid}-$_cue'),
                  initialValue: _cue,
                  decoration: const InputDecoration(
                    labelText: 'Manual fire cue',
                  ),
                  items: [
                    for (var cue = 1; cue <= selected.cueCount; cue++)
                      DropdownMenuItem(value: cue, child: Text('Cue $cue')),
                  ],
                  onChanged: enabled
                      ? (cue) => setState(() => _cue = cue!)
                      : null,
                ),
                const SizedBox(height: 8),
                FilledButton.icon(
                  onPressed:
                      enabled &&
                          master.armed.contains(selected.uid) &&
                          !master.running &&
                          !master.stepping
                      ? () => _run(() => master.fire(selected.uid, _cue))
                      : null,
                  icon: const Icon(Icons.flash_on),
                  label: Text('FIRE group ${selected.group} cue $_cue'),
                ),
                ExpansionTile(
                  // Without its own key the tile shares the page list's
                  // PageStorage slot and reads the scroll offset as a bool.
                  key: PageStorageKey('master-module-tools-${selected.uid}'),
                  title: const Text('Module diagnostics and maintenance'),
                  children: [
                    Wrap(
                      spacing: 8,
                      runSpacing: 8,
                      children: [
                        button(
                          'Speed test',
                          maintenance ? () => master.ping(selected.uid) : null,
                        ),
                        button(
                          'Range test (20 pings)',
                          maintenance
                              ? () => master.ping(selected.uid, count: 20)
                              : null,
                        ),
                        button(
                          'Raw ADC',
                          maintenance
                              ? () => master.rawResistance(selected.uid)
                              : null,
                        ),
                        button('Calibration', maintenance ? _calibrate : null),
                        button(
                          'Set unique ID',
                          maintenance
                              ? () => _setting(
                                  0x30,
                                  'Module unique ID',
                                  'Decimal or 0x hexadecimal',
                                )
                              : null,
                        ),
                        button(
                          'Set network',
                          maintenance
                              ? () =>
                                    _setting(0x32, 'Module network', '0–65535')
                              : null,
                        ),
                        button(
                          'Set channel',
                          maintenance
                              ? () => _setting(
                                  0x33,
                                  'Module channel',
                                  '0 or 74–100',
                                )
                              : null,
                        ),
                        button(
                          'Update module file',
                          maintenance ? _update : null,
                        ),
                        button(
                          'Reboot module',
                          maintenance
                              ? () => master.rebootModule(selected.uid)
                              : null,
                        ),
                      ],
                    ),
                  ],
                ),
              ],
              ExpansionTile(
                key: const PageStorageKey('master-radio-tools'),
                title: const Text('Radio settings and maintenance'),
                children: [
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: [
                      button(
                        'Set radio network',
                        maintenance
                            ? () async {
                                final text = await _input(
                                  'Radio network',
                                  '0–65535',
                                );
                                if (text == null) return;
                                final value = _number(text);
                                if (value < 0 || value > 65535) {
                                  throw const FormatException(
                                    'Network must be 0–65535',
                                  );
                                }
                                await master.bridgeCommand(
                                  0x12,
                                  bigEndian(value, 2),
                                );
                              }
                            : null,
                      ),
                      button(
                        'Set radio channel',
                        maintenance
                            ? () async {
                                final text = await _input(
                                  'Radio channel',
                                  '0 or 74–100',
                                );
                                if (text == null) return;
                                final value = _number(text);
                                if (value != 0 && (value < 74 || value > 100)) {
                                  throw const FormatException(
                                    'Use 0 or 74–100',
                                  );
                                }
                                await master.bridgeCommand(0x13, [value]);
                              }
                            : null,
                      ),
                      button(
                        'Scan channels',
                        maintenance ? () => master.bridgeCommand(0x14) : null,
                      ),
                      button(
                        'Clear radio statistics',
                        maintenance ? () => master.bridgeCommand(0x16) : null,
                      ),
                      button(
                        'Update bridge file',
                        maintenance ? () => _update(bridge: true) : null,
                      ),
                      button(
                        'Reset radio',
                        maintenance ? () => master.bridgeCommand(0x31) : null,
                      ),
                    ],
                  ),
                  if (session.values['Channel scan'] != null)
                    Text(session.values['Channel scan']!),
                ],
              ),
            ] else ...[
              const SizedBox(height: 8),
              Text(
                master.document == null
                    ? 'No script uploaded in this USB session'
                    : 'Uploaded: ${master.document!.name} • ${master.uploaded.length} modules',
              ),
              if (master.running)
                Text(
                  'Script clock: ${(master.elapsedMs / 1000).toStringAsFixed(1)} s',
                ),
              if (master.stepping)
                Text(
                  'Next event: ${master.stepIndex + 1}/${master.eventTimes.length}',
                ),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  button(
                    'Upload loaded script',
                    maintenance && widget.script != null
                        ? () => master.uploadScript(widget.script!)
                        : null,
                    icon: Icons.upload,
                  ),
                  button(
                    'Clear uploaded script',
                    maintenance && master.uploaded.isNotEmpty
                        ? master.clearScript
                        : null,
                  ),
                  button(
                    'Start timed script',
                    enabled &&
                            identical(master.document, widget.script) &&
                            master.document != null &&
                            !master.running &&
                            !master.stepping
                        ? () => master.startScript()
                        : null,
                    icon: Icons.play_arrow,
                  ),
                  button(
                    'Start at offset',
                    enabled &&
                            identical(master.document, widget.script) &&
                            master.document != null &&
                            !master.running &&
                            !master.stepping
                        ? () async {
                            final text = await _input(
                              'Script offset',
                              'Milliseconds',
                              initial: '0',
                            );
                            if (text != null) {
                              await master.startScript(offsetMs: _number(text));
                            }
                          }
                        : null,
                  ),
                  button(
                    'Prepare step mode',
                    enabled &&
                            identical(master.document, widget.script) &&
                            master.document != null &&
                            !master.running &&
                            !master.stepping
                        ? () => master.startScript(step: true)
                        : null,
                  ),
                  button(
                    'FIRE next script event',
                    enabled &&
                            master.stepping &&
                            master.stepIndex < master.eventTimes.length
                        ? master.nextStep
                        : null,
                    icon: Icons.skip_next,
                  ),
                ],
              ),
              const Text(
                'Arm each script module in Modules after upload. Step transmissions have no field acknowledgement in this protocol.',
              ),
            ],
          ],
        ),
      ),
    );
  }
}
