import 'package:flutter/material.dart';

import '../controller/radio_session.dart';
import 'fire_pulse_setting.dart';
import 'led_brightness_setting.dart';

/// App settings.
class SettingsPanel extends StatelessWidget {
  const SettingsPanel({super.key, required this.session});
  final RadioSession? session;

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text('Modules', style: Theme.of(context).textTheme.titleLarge),
        SwitchListTile(
          key: const ValueKey('auto-continuity'),
          contentPadding: EdgeInsets.zero,
          title: const Text('Automatic continuity checks'),
          subtitle: const Text(
            'After discovery, check one module each heartbeat while nothing '
            'is armed. Its light flashes green when the reply arrives.',
          ),
          value: session?.autoContinuity ?? true,
          onChanged: session == null
              ? null
              : (enabled) => session!.autoContinuity = enabled,
        ),
        const SizedBox(height: 12),
        FirePulseSetting(session: session),
        const SizedBox(height: 24),
        Text('Command module', style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 4),
        LedBrightnessSetting(session: session),
      ],
    );
  }
}
