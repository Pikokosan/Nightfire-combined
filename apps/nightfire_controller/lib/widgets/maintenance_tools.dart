import 'dart:convert';

import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';
import '../protocol/nfcp_codec.dart';

/// Decimal or 0x hexadecimal.
int parseNumber(String text) => text.trim().toLowerCase().startsWith('0x')
    ? int.parse(text.trim().substring(2), radix: 16)
    : int.parse(text.trim());

Future<String?> askValue(
  BuildContext context,
  String title,
  String hint, {
  String initial = '',
  int lines = 1,
}) {
  final controller = TextEditingController(text: initial);
  return showDialog<String>(
    context: context,
    builder: (context) => AlertDialog(
      title: Text(title),
      content: TextField(
        controller: controller,
        decoration: InputDecoration(labelText: hint),
        maxLines: lines,
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
}

Future<void> _run(
  BuildContext context,
  Future<dynamic> Function() action,
) async {
  try {
    await action();
  } catch (error) {
    if (context.mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('$error')));
    }
  }
}

Widget _button(
  BuildContext context,
  String text,
  Future<dynamic> Function()? action,
) => OutlinedButton.icon(
  onPressed: action == null ? null : () => _run(context, action),
  icon: const Icon(Icons.tune),
  label: Text(text),
);

/// Maintenance is only offered while nothing is armed or running.
bool _maintenance(RadioSession session) {
  final master = session.master;
  return master.available &&
      master.armed.isEmpty &&
      !master.running &&
      !master.stepping;
}

/// Diagnostics and settings for one field module (its card's Settings).
class ModuleMaintenance extends StatelessWidget {
  const ModuleMaintenance({
    super.key,
    required this.session,
    required this.module,
  });
  final RadioSession session;
  final FieldModule module;

  Future<void> _setting(
    BuildContext context,
    int command,
    String name,
    String hint,
  ) async {
    final value = await askValue(context, name, hint);
    if (value == null || !context.mounted) return;
    await session.master.setModuleValue(
      module.uid,
      command,
      parseNumber(value),
    );
  }

  Future<void> _calibrate(BuildContext context) async {
    final bank = await askValue(
      context,
      'Calibration bank',
      'Bank index (0-based)',
      initial: '0',
    );
    if (bank == null || !context.mounted) return;
    final text = await askValue(
      context,
      'Calibration points',
      'One ADC mV,deci-ohms pair per line',
      lines: 8,
    );
    if (text == null || !context.mounted) return;
    final points = const LineSplitter()
        .convert(text)
        .where((s) => s.trim().isNotEmpty)
        .map((line) {
          final values = line.split(',');
          if (values.length != 2) {
            throw const FormatException('Use ADC mV,deci-ohms');
          }
          return (parseNumber(values[0]), parseNumber(values[1]));
        })
        .toList();
    await session.master.calibrate(module.uid, parseNumber(bank), points);
  }

  @override
  Widget build(BuildContext context) {
    final master = session.master;
    if (!master.supported) {
      return const Text(
        'Diagnostics and maintenance need usb-master-0.4 bridge firmware.',
      );
    }
    final enabled = _maintenance(session);
    final uid = module.uid;
    Widget button(String text, Future<dynamic> Function() action) =>
        _button(context, text, enabled ? action : null);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Diagnostics and maintenance',
          style: Theme.of(context).textTheme.titleMedium,
        ),
        const SizedBox(height: 4),
        Text(
          enabled ? 'Available while nothing is armed or running.' : 'Unavailable while a module is armed, a script is running or the radio is busy.',
        ),
        const SizedBox(height: 8),
        Builder(
          builder: (context) {
            final output = module.outputStatus;
            return Text(
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
            );
          },
        ),
        TextButton.icon(
          onPressed: master.available
              ? () => _run(context, () => master.refreshOutputStatus(uid))
              : null,
          icon: const Icon(Icons.sync),
          label: const Text('Refresh physical output state'),
        ),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            button('Speed test', () => master.ping(uid)),
            button('Range test (20 pings)', () => master.ping(uid, count: 20)),
            button('Raw ADC', () => master.rawResistance(uid)),
            button('Calibration', () => _calibrate(context)),
            button(
              'Set unique ID',
              () => _setting(
                context,
                0x30,
                'Module unique ID',
                'Decimal or 0x hexadecimal',
              ),
            ),
            button(
              'Set network',
              () => _setting(context, 0x32, 'Module network', '0–65535'),
            ),
            button(
              'Set channel',
              () => _setting(context, 0x33, 'Module channel', '0 or 74–100'),
            ),
            button('Reboot module', () => master.rebootModule(uid)),
          ],
        ),
      ],
    );
  }
}

/// Settings and maintenance for the USB radio (Radio page).
class RadioMaintenance extends StatelessWidget {
  const RadioMaintenance({super.key, required this.session});
  final RadioSession session;

  @override
  Widget build(BuildContext context) {
    final master = session.master;
    if (!master.supported) return const SizedBox.shrink();
    final enabled = _maintenance(session);
    Widget button(String text, Future<dynamic> Function() action) =>
        _button(context, text, enabled ? action : null);
    return ExpansionTile(
      key: const PageStorageKey('radio-tools'),
      title: const Text('Radio settings and maintenance'),
      expandedCrossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            button('Set radio network', () async {
              final text = await askValue(context, 'Radio network', '0–65535');
              if (text == null) return;
              final value = parseNumber(text);
              if (value < 0 || value > 65535) {
                throw const FormatException('Network must be 0–65535');
              }
              await master.bridgeCommand(0x12, bigEndian(value, 2));
            }),
            button('Set radio channel', () async {
              final text = await askValue(
                context,
                'Radio channel',
                '0 or 74–100',
              );
              if (text == null) return;
              final value = parseNumber(text);
              if (value != 0 && (value < 74 || value > 100)) {
                throw const FormatException('Use 0 or 74–100');
              }
              await master.bridgeCommand(0x13, [value]);
            }),
            button('Scan channels', () => master.bridgeCommand(0x14)),
            button('Clear radio statistics', () => master.bridgeCommand(0x16)),
            button('Reset radio', () => master.bridgeCommand(0x31)),
          ],
        ),
        if (session.values['Channel scan'] != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(session.values['Channel scan']!),
          ),
        const SizedBox(height: 8),
      ],
    );
  }
}
