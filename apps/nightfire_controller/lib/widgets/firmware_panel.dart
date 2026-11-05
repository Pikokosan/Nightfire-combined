import 'package:file_selector/file_selector.dart';
import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import '../protocol/bridge_codec.dart';

typedef PickedFile = ({String name, List<int> bytes});

/// Opens the platform file picker. Replaced in tests.
typedef FirmwarePicker = Future<List<PickedFile>> Function({bool multiple});

Future<List<PickedFile>> pickFirmwareFiles({bool multiple = false}) async {
  final files = multiple
      ? await openFiles()
      : [?await openFile()];
  return [
    for (final file in files)
      if (await file.length() <= 256 * 1024)
        (name: restorePickedName(file.name), bytes: await file.readAsBytes())
      else
        throw FormatException('${file.name} is larger than 256 KiB'),
  ];
}

/// Android's picker copies a file whose type it does not know (.mpy) under a
/// MIME-derived name, so usb_master_bundle.mpy arrives as
/// usb_master_bundle.bin. MicroPython only imports .py and .mpy, so a .bin is
/// a renamed compiled module.
String restorePickedName(String name) {
  final dot = name.lastIndexOf('.');
  if (dot <= 0 || name.substring(dot).toLowerCase() != '.bin') return name;
  return '${name.substring(0, dot)}.mpy';
}

/// Firmware updates for the USB radio bridge and the field modules.
class FirmwarePanel extends StatefulWidget {
  const FirmwarePanel({
    super.key,
    required this.session,
    this.pick = pickFirmwareFiles,
    this.onPickingFile,
  });
  final RadioSession session;
  final FirmwarePicker pick;
  final ValueChanged<bool>? onPickingFile;

  @override
  State<FirmwarePanel> createState() => _FirmwarePanelState();
}

class _FirmwarePanelState extends State<FirmwarePanel> {
  final _bridgeFiles = <String, List<int>>{};
  PickedFile? _moduleFile;
  String _target = 'field_main.mpy';
  final _selected = <int>{};

  Future<List<PickedFile>> _pick({bool multiple = false}) async {
    widget.onPickingFile?.call(true);
    try {
      return await widget.pick(multiple: multiple);
    } finally {
      widget.onPickingFile?.call(false);
    }
  }

  Future<void> _run(Future<void> Function() action) async {
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

  Future<void> _chooseBridgeFiles() async {
    final files = await _pick(multiple: true);
    if (files.isEmpty || !mounted) return;
    final rejected = [
      for (final file in files)
        if (!bridgeFirmwareFiles.contains(file.name)) file.name,
    ];
    if (rejected.isNotEmpty) {
      throw FormatException(
        'Not radio firmware: ${rejected.join(', ')}. '
        'Choose files from dist/android/usb-master-bundle.',
      );
    }
    setState(() {
      _bridgeFiles
        ..clear()
        ..addEntries(files.map((f) => MapEntry(f.name, f.bytes)));
    });
  }

  Future<void> _chooseModuleFile() async {
    final files = await _pick();
    if (files.isEmpty || !mounted) return;
    setState(() {
      _moduleFile = files.single;
      _target = files.single.name;
    });
  }

  // A dialog rather than an always-mounted TextField: this page stays built
  // in the navigation stack, and EditableText listens to app lifecycle.
  Future<void> _changeTarget() async {
    final controller = TextEditingController(text: _target);
    final value = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Install as'),
        content: TextField(
          controller: controller,
          autofocus: true,
          decoration: const InputDecoration(
            labelText: 'File name on the module',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, controller.text.trim()),
            child: const Text('Apply'),
          ),
        ],
      ),
    );
    if (value != null && value.isNotEmpty && mounted) {
      setState(() => _target = value);
    }
  }

  String _size(int bytes) => bytes >= 1024
      ? '${(bytes / 1024).toStringAsFixed(1)} KiB'
      : '$bytes bytes';

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    final master = session.master;
    final idle =
        master.available &&
        master.armed.isEmpty &&
        !master.running &&
        !master.stepping;
    final updating = session.busy && master.updateProgress != null;
    final modules = session.modules.values.toList()
      ..sort((a, b) => a.group.compareTo(b.group));
    _selected.removeWhere((uid) => !session.modules.containsKey(uid));
    final bundle = _bridgeFiles['usb_master_bundle.mpy'];
    final newVersion = bundle == null ? null : embeddedBridgeVersion(bundle);
    final theme = Theme.of(context);

    Widget progress() => Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          LinearProgressIndicator(value: master.updateProgress),
          const SizedBox(height: 4),
          Text(master.status),
        ],
      ),
    );

    final stateNote = !session.ready
        ? 'Connect the radio in Radio first.'
        : !master.supported
        ? 'Firmware updates need usb-master-0.4 bridge firmware.'
        : master.armed.isNotEmpty || master.running || master.stepping
        ? 'Disarm all modules before updating firmware.'
        : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        if (stateNote != null)
          Card(
            color: theme.colorScheme.secondaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Text(stateNote),
            ),
          ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Radio bridge', style: theme.textTheme.titleLarge),
                const SizedBox(height: 4),
                Text(
                  'Installed: ${session.values['Firmware'] ?? 'not connected'}',
                ),
                const SizedBox(height: 8),
                const Text(
                  'Choose main.py and usb_master_bundle.mpy from '
                  'dist/android/usb-master-bundle. Network, channel and LED '
                  'settings on the radio are kept.',
                ),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    OutlinedButton.icon(
                      onPressed: session.busy
                          ? null
                          : () => _run(_chooseBridgeFiles),
                      icon: const Icon(Icons.folder_open),
                      label: const Text('Choose radio files'),
                    ),
                    FilledButton.icon(
                      onPressed: idle && _bridgeFiles.isNotEmpty
                          ? () => _run(() async {
                              await master.updateBridgeFirmware(
                                Map.of(_bridgeFiles),
                              );
                            })
                          : null,
                      icon: const Icon(Icons.system_update_alt),
                      label: const Text('Install on radio'),
                    ),
                  ],
                ),
                for (final entry in _bridgeFiles.entries)
                  Text('• ${entry.key} (${_size(entry.value.length)})'),
                if (newVersion != null) Text('New version: $newVersion'),
                if (updating && master.status.startsWith('Radio update'))
                  progress(),
                if (master.bridgeUpdateResult != null) ...[
                  const SizedBox(height: 8),
                  Text(
                    master.bridgeUpdateResult!,
                    style: TextStyle(
                      color: master.bridgeUpdateResult!.startsWith('Radio updated')
                          ? Colors.lightGreen
                          : Colors.amber,
                    ),
                  ),
                ],
                const SizedBox(height: 8),
                Text(
                  'The radio restarts after installing. If the new firmware '
                  'cannot connect to this app, the radio puts the previous '
                  'version back the next time it powers up.',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Field modules', style: theme.textTheme.titleLarge),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    OutlinedButton.icon(
                      onPressed: session.busy
                          ? null
                          : () => _run(_chooseModuleFile),
                      icon: const Icon(Icons.folder_open),
                      label: const Text('Choose module firmware'),
                    ),
                    if (_moduleFile != null)
                      Text(
                        '${_moduleFile!.name} '
                        '(${_size(_moduleFile!.bytes.length)})',
                      ),
                  ],
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Flexible(child: Text('Install as: $_target')),
                    TextButton(
                      onPressed: session.busy ? null : _changeTarget,
                      child: const Text('Change'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                if (modules.isEmpty)
                  const Text('Discover modules in Modules to update them.')
                else ...[
                  Row(
                    children: [
                      TextButton(
                        onPressed: session.busy
                            ? null
                            : () => setState(() {
                                _selected.length == modules.length
                                    ? _selected.clear()
                                    : _selected.addAll(
                                        modules.map((m) => m.uid),
                                      );
                              }),
                        child: Text(
                          _selected.length == modules.length
                              ? 'Select none'
                              : 'Select all',
                        ),
                      ),
                    ],
                  ),
                  for (final module in modules)
                    CheckboxListTile(
                      dense: true,
                      contentPadding: EdgeInsets.zero,
                      controlAffinity: ListTileControlAffinity.leading,
                      value: _selected.contains(module.uid),
                      onChanged: session.busy
                          ? null
                          : (value) => setState(() {
                              value == true
                                  ? _selected.add(module.uid)
                                  : _selected.remove(module.uid);
                            }),
                      title: Text('Group ${module.group} • ${module.id}'),
                      subtitle: Text(
                        [
                          'Firmware ${module.info['Firmware'] ?? 'unknown'}',
                          ?master.firmwareResults[module.uid],
                        ].join(' • '),
                        style: TextStyle(
                          color:
                              master.firmwareResults[module.uid]?.startsWith(
                                    'Failed',
                                  ) ==
                                  true
                              ? Colors.red.shade300
                              : null,
                        ),
                      ),
                    ),
                ],
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    FilledButton.icon(
                      onPressed:
                          idle && _moduleFile != null && _selected.isNotEmpty
                          ? () => _run(() async {
                              await master.updateModuleFirmware(
                                [
                                  for (final m in modules)
                                    if (_selected.contains(m.uid)) m.uid,
                                ],
                                _target,
                                _moduleFile!.bytes,
                              );
                            })
                          : null,
                      icon: const Icon(Icons.system_update_alt),
                      label: Text(
                        _selected.isEmpty
                            ? 'Update modules'
                            : 'Update ${_selected.length} module(s)',
                      ),
                    ),
                    if (updating)
                      OutlinedButton.icon(
                        onPressed: master.cancelUpdate,
                        icon: const Icon(Icons.cancel),
                        label: const Text('Cancel'),
                      ),
                  ],
                ),
                if (updating && !master.status.startsWith('Radio update'))
                  progress(),
                const SizedBox(height: 8),
                Text(
                  'Modules must be disarmed. Each module checks the file\'s '
                  'size and checksum before replacing its copy, then restarts '
                  'and is checked for its firmware version and safe outputs. '
                  'A cancelled or failed transfer leaves the old firmware.',
                  style: theme.textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}
