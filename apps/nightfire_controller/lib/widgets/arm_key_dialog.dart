import 'package:flutter/material.dart';

import '../controller/radio_session.dart';

/// Shown while the bridge's ARM key was on when the radio connected (or when
/// the bridge powered up). Arming stays blocked until the key is turned to
/// SAFE; the dialog then closes itself.
class ArmKeyDialog extends StatefulWidget {
  const ArmKeyDialog({super.key, required this.session});
  final RadioSession session;

  @override
  State<ArmKeyDialog> createState() => _ArmKeyDialogState();
}

class _ArmKeyDialogState extends State<ArmKeyDialog> {
  bool _closing = false;

  @override
  void initState() {
    super.initState();
    widget.session.addListener(_changed);
  }

  @override
  void dispose() {
    widget.session.removeListener(_changed);
    super.dispose();
  }

  void _changed() {
    if (!mounted || _closing) return;
    if (!widget.session.armKeyLockout || !widget.session.ready) {
      _closing = true;
      Navigator.of(context).pop();
    } else {
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return PopScope(
      canPop: false,
      child: AlertDialog(
        key: const ValueKey('arm-key-dialog'),
        icon: Icon(
          Icons.warning_amber_rounded,
          color: theme.colorScheme.error,
          size: 48,
        ),
        title: const Text('Turn the ARM key off'),
        content: const Text(
          'The ARM key on the radio was on when it connected. '
          'Turn the key to SAFE.\n\n'
          'Arming is blocked until the key has been turned to SAFE. '
          'This message closes by itself when it is.',
        ),
        actions: [
          TextButton(
            onPressed: () {
              _closing = true;
              Navigator.of(context).pop(true);
            },
            child: const Text('Hide (arming stays blocked)'),
          ),
        ],
      ),
    );
  }
}
