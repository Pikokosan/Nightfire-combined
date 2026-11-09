import 'package:flutter/material.dart';

import '../controller/radio_session.dart';

class CommandInterfacePanel extends StatelessWidget {
  const CommandInterfacePanel({super.key, required this.session});
  final RadioSession session;
  @override
  Widget build(BuildContext context) {
    final status = session.interfaceStatus;
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'Command interface',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 8),
            if (status == null)
              const Text(
                'Hardware status unavailable. Remote arm state remains unknown.',
              )
            else ...[
              Text(
                'Physical switch: ${session.master.supported && status.switchPosition == 2 ? "ARM position" : status.switchText}',
              ),
              Text('Status LEDs: ${status.ledText}'),
              Text('${status.sessionText} • ${status.timeoutMs} ms timeout'),
              if (session.masterHeartbeatStatus case final heartbeat?) ...[
                Text(heartbeat.text),
                Text('RF transmit failures: ${heartbeat.failures}'),
                const Text(
                  'Broadcast transmission does not confirm module reception.',
                ),
              ] else
                const Text(
                  'RF heartbeat status unavailable • radio firmware update required.',
                ),
              if (status.faults != 0)
                const Text(
                  'Interface fault • inspect wiring and connection',
                  style: TextStyle(color: Colors.amber),
                ),
              Text(
                session.master.supported
                    ? 'Live controls are in Fire and Scripts. ${session.master.armSummary}'
                    : 'Arming and firing are disabled in this interface stage.',
              ),
            ],
          ],
        ),
      ),
    );
  }
}
