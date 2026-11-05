import 'dart:async';

import 'package:flutter/material.dart';

enum SimulatedArmSource { software, hardware }

/// An isolated visual demo with invented cues and no hardware dependencies.
class OfflineControlSimulation extends StatefulWidget {
  const OfflineControlSimulation({super.key});

  @override
  State<OfflineControlSimulation> createState() =>
      _OfflineControlSimulationState();
}

class _OfflineControlSimulationState extends State<OfflineControlSimulation> {
  bool _armed = false;
  SimulatedArmSource _armSource = SimulatedArmSource.software;
  int? _selected, _last;
  bool _dim = false;
  Timer? _blink;

  void _simulate() {
    if (!_armed || _selected == null) return;
    _blink?.cancel();
    setState(() {
      _last = _selected;
      _dim = false;
    });
    if (MediaQuery.disableAnimationsOf(context)) return;
    var ticks = 0;
    _blink = Timer.periodic(const Duration(milliseconds: 500), (timer) {
      ticks++;
      setState(() => _dim = ticks.isOdd);
      if (ticks == 6) timer.cancel();
    });
  }

  @override
  void dispose() {
    _blink?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Offline arm/fire simulation'),
    content: SizedBox(
      width: 640,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'DUMMY CUES ONLY • no radio commands or physical outputs',
            ),
            ExpansionTile(
              key: const PageStorageKey('offline-simulator-settings'),
              tilePadding: EdgeInsets.zero,
              leading: const Icon(Icons.settings_outlined),
              title: const Text('Simulator settings'),
              subtitle: Text(
                _armSource == SimulatedArmSource.software
                    ? 'Software arm switch (simulated)'
                    : 'Hardware arm switch (simulated)',
              ),
              childrenPadding: const EdgeInsets.only(bottom: 12),
              children: [
                DropdownButtonFormField<SimulatedArmSource>(
                  key: ValueKey(_armSource),
                  initialValue: _armSource,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Simulated arm source',
                    border: OutlineInputBorder(),
                  ),
                  items: const [
                    DropdownMenuItem(
                      value: SimulatedArmSource.software,
                      child: Text('Software arm switch'),
                    ),
                    DropdownMenuItem(
                      value: SimulatedArmSource.hardware,
                      child: Text('Hardware arm switch'),
                    ),
                  ],
                  onChanged: (source) {
                    if (source == null || source == _armSource) return;
                    setState(() {
                      _armSource = source;
                      _armed = false;
                      _selected = null;
                    });
                  },
                ),
                const SizedBox(height: 8),
                const Text(
                  'Simulator only. Changing the source resets simulated arming and cue selection. No physical switch is read or configured.',
                ),
              ],
            ),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(
                _armSource == SimulatedArmSource.software
                    ? 'Simulated arm'
                    : 'Simulated hardware input',
              ),
              subtitle: Text(
                _armed ? 'Simulation: armed' : 'Simulation: disarmed',
              ),
              value: _armed,
              onChanged: (value) => setState(() => _armed = value),
            ),
            const Text('Select a dummy cue, then simulate a fire press.'),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                for (var cue = 1; cue <= 4; cue++)
                  Opacity(
                    key: ValueKey('demo-opacity-$cue'),
                    opacity: _last == cue && _dim ? 0.35 : 1,
                    child: SizedBox(
                      width: 145,
                      height: 104,
                      child: OutlinedButton(
                        key: ValueKey('demo-cue-$cue'),
                        style: OutlinedButton.styleFrom(
                          side: BorderSide(
                            color: _last == cue
                                ? Colors.orange
                                : Theme.of(context).colorScheme.outline,
                            width: _last == cue || _selected == cue ? 3 : 1,
                          ),
                        ),
                        onPressed: () => setState(() => _selected = cue),
                        child: Text(
                          'Demo cue $cue${_selected == cue ? '\nSelected' : ''}${_last == cue ? '\nLast simulated' : ''}',
                        ),
                      ),
                    ),
                  ),
              ],
            ),
            const SizedBox(height: 12),
            FilledButton.icon(
              onPressed: _armed && _selected != null ? _simulate : null,
              icon: const Icon(Icons.touch_app_outlined),
              label: const Text('Simulate fire'),
            ),
            if (_last != null)
              Text('Last simulated: Demo cue $_last • no command sent'),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.of(context).pop(),
        child: const Text('Close simulation'),
      ),
    ],
  );
}
