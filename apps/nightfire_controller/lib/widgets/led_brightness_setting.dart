import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../controller/radio_session.dart';

/// The slider shows 1-100 %, mapped to the bridge's 1-255 on a square curve
/// so the dim end (where these LEDs are normally run) gets most of the
/// travel. The default bridge level, 12, is 22 %.
int ledLevelFromPercent(int percent) =>
    (255 * math.pow(percent.clamp(1, 100) / 100, 2)).round().clamp(1, 255);

int ledPercentFromLevel(int level) =>
    (100 * math.sqrt(level.clamp(1, 255) / 255)).round().clamp(1, 100);

/// Settings row for the command module's status LED brightness.
class LedBrightnessSetting extends StatefulWidget {
  const LedBrightnessSetting({super.key, required this.session});
  final RadioSession? session;

  @override
  State<LedBrightnessSetting> createState() => _LedBrightnessSettingState();
}

class _LedBrightnessSettingState extends State<LedBrightnessSetting> {
  double? _dragging;
  bool _saving = false;
  String? _error;

  Future<void> _save(int percent) async {
    final session = widget.session;
    if (session == null) return;
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await session.setLedBrightness(ledLevelFromPercent(percent));
    } catch (error) {
      _error = error is StateError ? error.message : '$error';
    } finally {
      if (mounted) {
        setState(() {
          _saving = false;
          _dragging = null;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final session = widget.session;
    if (session == null) return _body(null);
    return ListenableBuilder(
      listenable: session,
      builder: (context, _) => _body(session),
    );
  }

  Widget _body(RadioSession? session) {
    final level = session?.ledBrightness;
    final supported = session?.supportsLedBrightness == true;
    final armed = session?.master.armed.isNotEmpty == true;
    final enabled = supported && level != null && !armed && !_saving;
    final percent = _dragging ?? ledPercentFromLevel(level ?? 12).toDouble();
    final String note;
    if (session == null || !session.ready) {
      note = 'Connect to the radio to change it.';
    } else if (!supported) {
      note = 'Needs radio firmware usb-master-0.4.4 or newer.';
    } else if (armed) {
      note = 'Disarm all modules to change it.';
    } else {
      note = 'Saved on the radio, so it stays after a restart.';
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            const Expanded(child: Text('Status LED brightness')),
            Text(
              supported && level != null ? '${percent.round()} %' : '—',
              key: const ValueKey('led-brightness-value'),
            ),
          ],
        ),
        Slider(
          key: const ValueKey('led-brightness'),
          min: 1,
          max: 100,
          divisions: 99,
          value: percent,
          label: '${percent.round()} %',
          onChanged: enabled ? (value) => setState(() => _dragging = value) : null,
          onChangeEnd: enabled ? (value) => _save(value.round()) : null,
        ),
        Text(note, style: Theme.of(context).textTheme.bodySmall),
        if (_error != null)
          Text(
            _error!,
            key: const ValueKey('led-brightness-error'),
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
      ],
    );
  }
}
