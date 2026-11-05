import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';

class ModuleSettings extends StatefulWidget {
  const ModuleSettings({
    super.key,
    required this.module,
    required this.session,
  });
  final FieldModule module;
  final RadioSession session;
  @override
  State<ModuleSettings> createState() => _ModuleSettingsState();
}

class _ModuleSettingsState extends State<ModuleSettings> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _group;
  bool _saving = false;
  String? _message;

  @override
  void initState() {
    super.initState();
    _group = TextEditingController(text: '${widget.module.group}');
  }

  @override
  void dispose() {
    _group.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    if (!_form.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _message = null;
    });
    try {
      final saved = await widget.session.setModuleGroup(
        widget.module.uid,
        int.parse(_group.text),
      );
      if (mounted) {
        setState(
          () => _message = saved
              ? 'Group ${widget.module.group} saved and confirmed.'
              : widget.session.status,
        );
      }
    } catch (error) {
      if (mounted) setState(() => _message = '$error');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final module = widget.module;
    final enabled =
        !_saving &&
        !widget.session.busy &&
        widget.session.canSetModuleGroup &&
        module.seenInScan;
    return Form(
      key: _form,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Module settings • ${module.id}',
            style: Theme.of(context).textTheme.titleMedium,
          ),
          const SizedBox(height: 12),
          const Text(
            'The group number is saved on this module and maps to the script channel. The module must be disarmed.',
          ),
          const SizedBox(height: 12),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 320),
            child: TextFormField(
              key: ValueKey('group-input-${module.uid}'),
              controller: _group,
              enabled: enabled,
              keyboardType: TextInputType.number,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: InputDecoration(
                labelText: 'Group number',
                helperText: 'Current: ${module.group} • Range: 0–65535',
                border: const OutlineInputBorder(),
              ),
              onChanged: (_) => setState(() => _message = null),
              validator: (text) {
                final value = int.tryParse(text ?? '');
                if (value == null || value < 0 || value > 65535) {
                  return 'Enter a group from 0 to 65535.';
                }
                if (widget.session.modules.values.any(
                  (other) => other.uid != module.uid && other.group == value,
                )) {
                  return 'Another discovered module uses this group.';
                }
                return null;
              },
            ),
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: enabled && int.tryParse(_group.text) != module.group
                ? _save
                : null,
            icon: const Icon(Icons.save_outlined),
            label: Text(_saving ? 'Saving and confirming…' : 'Save group'),
          ),
          if (!widget.session.canSetModuleGroup)
            const Text(
              'Connect with radio firmware 0.3.2 or newer to change groups.',
            ),
          if (!module.seenInScan)
            const Text(
              'Run discovery to confirm this module is reachable before changing its group.',
            ),
          if (_message != null)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_message!),
            ),
        ],
      ),
    );
  }
}
