import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';

/// Settings row for how long a fired cue is held on. The value is saved in
/// the app and sent to every discovered field module, which saves it too.
class FirePulseSetting extends StatefulWidget {
  const FirePulseSetting({super.key, required this.session});
  final RadioSession? session;

  @override
  State<FirePulseSetting> createState() => _FirePulseSettingState();
}

class _FirePulseSettingState extends State<FirePulseSetting> {
  final _controller = TextEditingController();
  String? _invalid;

  @override
  void initState() {
    super.initState();
    _controller.text =
        '${widget.session?.firePulseMs ?? RadioSession.firePulseDefaultMs}';
    widget.session?.addListener(_syncText);
  }

  @override
  void didUpdateWidget(FirePulseSetting old) {
    super.didUpdateWidget(old);
    if (old.session != widget.session) {
      old.session?.removeListener(_syncText);
      widget.session?.addListener(_syncText);
      _syncText();
    }
  }

  @override
  void dispose() {
    widget.session?.removeListener(_syncText);
    _controller.dispose();
    super.dispose();
  }

  /// Picks up the saved value once preferences load, until the user types.
  bool _edited = false;
  void _syncText() {
    final value = widget.session?.firePulseMs;
    if (value == null || _edited) return;
    if (int.tryParse(_controller.text) != value) _controller.text = '$value';
  }

  int? _parse(String text) {
    final value = int.tryParse(text.trim());
    if (value == null ||
        value < RadioSession.firePulseMinMs ||
        value > RadioSession.firePulseMaxMs) {
      return null;
    }
    return value;
  }

  void _changed(String text) {
    final value = _parse(text);
    _edited = true;
    setState(() {
      _invalid = value == null
          ? 'Enter ${RadioSession.firePulseMinMs}-${RadioSession.firePulseMaxMs} ms'
          : null;
    });
    if (value != null) widget.session?.firePulseMs = value;
  }

  Future<void> _send() async {
    final session = widget.session;
    final value = _parse(_controller.text);
    if (session == null || value == null) return;
    FocusScope.of(context).unfocus();
    session.firePulseMs = value;
    await session.master.pushFirePulse(value);
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
    final modules = <FieldModule>[
      for (final module in session?.modules.values ?? const <FieldModule>[])
        if (module.seenInScan) module,
    ];
    final supported = session?.supportsFirePulse == true;
    final armed =
        session != null &&
        (session.master.armed.isNotEmpty ||
            session.master.running ||
            session.master.stepping);
    final canSend =
        session != null &&
        supported &&
        !armed &&
        modules.isNotEmpty &&
        _invalid == null &&
        session.master.available;
    final String note;
    if (session == null || !session.ready) {
      note = 'Connect to the radio to send it to the modules.';
    } else if (!supported) {
      note = 'Sending needs radio firmware usb-master-0.4.5 or newer.';
    } else if (armed) {
      note = 'Disarm all modules to change it.';
    } else if (modules.isEmpty) {
      note = 'Scan for modules, then send it.';
    } else {
      note =
          'Each module saves it, so it stays after a restart. '
          'Needs field firmware 0.3.1 or newer.';
    }
    final error = session?.master.lastError;
    final theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: TextField(
                key: const ValueKey('fire-pulse-ms'),
                controller: _controller,
                keyboardType: TextInputType.number,
                inputFormatters: [FilteringTextInputFormatter.digitsOnly],
                decoration: InputDecoration(
                  labelText: 'Cue hold time',
                  suffixText: 'ms',
                  helperText:
                      'How long a fired cue output stays on '
                      '(${RadioSession.firePulseMinMs}-${RadioSession.firePulseMaxMs} ms).',
                  errorText: _invalid,
                ),
                onChanged: _changed,
              ),
            ),
            const SizedBox(width: 12),
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: FilledButton(
                key: const ValueKey('fire-pulse-send'),
                onPressed: canSend ? _send : null,
                child: const Text('Send to modules'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 4),
        Text(note, style: theme.textTheme.bodySmall),
        for (final module in modules)
          if (module.firePulseMs != null || module.firePulseError != null)
            Text(
              '${module.id} (group ${module.group}): '
              '${module.firePulseError ?? '${module.firePulseMs} ms'}',
              key: ValueKey('fire-pulse-${module.id}'),
              style: theme.textTheme.bodySmall?.copyWith(
                color: module.firePulseError == null
                    ? null
                    : theme.colorScheme.error,
              ),
            ),
        if (error != null && error.startsWith('Send cue hold time'))
          Text(
            error,
            key: const ValueKey('fire-pulse-error'),
            style: TextStyle(color: theme.colorScheme.error),
          ),
      ],
    );
  }
}
