import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

import 'controller/radio_session.dart';
import 'transport/android_usb_transport.dart';
import 'transport/radio_transport.dart';
import 'widgets/field_modules_panel.dart';
import 'widgets/command_interface_panel.dart';
import 'transport/emulator_relay_transport.dart';
import 'widgets/controller_navigation.dart';
import 'widgets/scripts_panel.dart';
import 'scripts/script_document.dart';
import 'controller/field_module.dart';
import 'widgets/master_controls_panel.dart';
import 'widgets/manual_fire_panel.dart';

/// Index of the Radio tab in [ControllerNavigation.destinations].
const kRadioSection = 3;

void main() => runApp(const NightfireApp());

class NightfireApp extends StatelessWidget {
  const NightfireApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Nightfire',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      brightness: Brightness.dark,
      colorSchemeSeed: const Color(0xffff8a35),
      scaffoldBackgroundColor: const Color(0xff10151d),
      useMaterial3: true,
    ),
    home: const RadioPage(),
  );
}

class RadioPage extends StatefulWidget {
  const RadioPage({super.key, this.transport, this.pickScript});
  final RadioTransport? transport;
  final Future<ScriptSource?> Function()? pickScript;
  @override
  State<RadioPage> createState() => _RadioPageState();
}

class _RadioPageState extends State<RadioPage> with WidgetsBindingObserver {
  RadioSession? _session;
  List<RadioDevice> _devices = [];
  String? _selected;
  String? _scanError;
  bool _scanning = false;
  bool _useRelay = false;
  int _section = 0;
  ScriptDocument? _loadedScript;
  ModuleFocus? _moduleFocus;
  bool _scriptPickerOpen = false;
  bool _waitingForPickerResume = false;
  (RadioSession, String)? _pickerConnection;

  void _finishPickerCycle() {
    _waitingForPickerResume = false;
    final previous = _pickerConnection;
    _pickerConnection = null;
    // Android can abort the emulator relay's TCP socket while the external
    // picker is foregrounded. Recover only the connection this picker owned.
    if (previous != null &&
        identical(previous.$1, _session) &&
        !previous.$1.ready &&
        !previous.$1.busy) {
      unawaited(previous.$1.connect(previous.$2));
    }
  }

  void _pickingScriptChanged(bool open) {
    _scriptPickerOpen = open;
    if (open) {
      _waitingForPickerResume = true;
      final session = _session;
      _pickerConnection = session?.ready == true && _selected != null
          ? (session!, _selected!)
          : null;
    } else if (WidgetsBinding.instance.lifecycleState ==
        AppLifecycleState.resumed) {
      _finishPickerCycle();
    }
    // Android may deliver the picker result before the activity resumes.
    // Retain the exemption until both the result and resumed state arrive.
  }

  Widget _page(String name, List<Widget> children) => Center(
    child: ConstrainedBox(
      constraints: const BoxConstraints(maxWidth: 1120),
      child: ListView(
        primary: false,
        key: PageStorageKey(name),
        padding: EdgeInsets.all(
          MediaQuery.sizeOf(context).width < 600 ? 16 : 24,
        ),
        children: children,
      ),
    ),
  );

  void _selectTransport(bool relay) {
    _session?.removeListener(_update);
    _session?.dispose();
    _session = RadioSession(
      relay ? EmulatorRelayTransport() : AndroidUsbTransport(),
    )..addListener(_update);
    setState(() {
      _useRelay = relay;
      _devices = [];
      _selected = null;
    });
    unawaited(_scan());
  }

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    if (widget.transport != null || Platform.isAndroid) {
      _session = RadioSession(widget.transport ?? AndroidUsbTransport())
        ..addListener(_update);
      unawaited(_scan());
    }
  }

  void _update() {
    if (mounted) setState(() {});
  }

  Future<void> _scan() async {
    if (_scanning || _session == null) return;
    setState(() {
      _scanning = true;
      _scanError = null;
    });
    try {
      final devices = await _session!.transport.devices();
      if (!mounted) return;
      setState(() {
        _devices = devices;
        _selected = devices.isEmpty ? null : devices.first.id;
      });
    } catch (error) {
      if (mounted) setState(() => _scanError = '$error');
    } finally {
      if (mounted) setState(() => _scanning = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && !_scriptPickerOpen) {
      if (_waitingForPickerResume) _finishPickerCycle();
    }
    if (state == AppLifecycleState.detached) _pickerConnection = null;
    if (state == AppLifecycleState.detached ||
        (state == AppLifecycleState.paused && !_waitingForPickerResume)) {
      unawaited(_session?.disconnect());
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _session?.removeListener(_update);
    _session?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final session = _session;
    final busy = session?.busy ?? false;
    final connectionPage = Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 850),
        child: ListView(
          primary: false,
          key: const PageStorageKey('radio'),
          padding: EdgeInsets.all(
            MediaQuery.sizeOf(context).width < 600 ? 16 : 24,
          ),
          children: [
            Text(
              'Radio connection',
              style: Theme.of(context).textTheme.headlineLarge,
            ),
            const SizedBox(height: 8),
            const Text('USB connection • Nightfire master controller'),
            const SizedBox(height: 16),
            if (session == null)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(20),
                  child: Text(
                    'The shared application is ready for this platform. Its USB adapter is not implemented yet. Android USB is the first supported connection.',
                  ),
                ),
              )
            else ...[
              if (kDebugMode && Platform.isAndroid)
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Local emulator relay'),
                  subtitle: const Text(
                    'Development only • PC USB serial connection; physical devices use direct USB',
                  ),
                  value: _useRelay,
                  onChanged: busy || session.ready ? null : _selectTransport,
                ),
              if (_useRelay)
                const Text(
                  'DEVELOPMENT RELAY — this is not Android USB passthrough.',
                  style: TextStyle(color: Colors.amber),
                ),
              if (_devices.isNotEmpty)
                DropdownButtonFormField<String>(
                  key: ValueKey('$_useRelay-$_selected'),
                  initialValue: _selected,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'USB radio'),
                  items: _devices
                      .map(
                        (device) => DropdownMenuItem(
                          value: device.id,
                          child: Text(
                            device.label,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                      )
                      .toList(),
                  onChanged: busy || session.ready
                      ? null
                      : (value) => setState(() => _selected = value),
                ),
              if (_devices.isEmpty)
                const Text(
                  'No Nightfire USB radio found. Attach the nice!nano with a USB data cable, then scan.',
                ),
              if (_scanError != null)
                Text(_scanError!, style: const TextStyle(color: Colors.amber)),
              const SizedBox(height: 16),
              Wrap(
                spacing: 12,
                runSpacing: 12,
                children: [
                  OutlinedButton.icon(
                    onPressed: busy || _scanning || session.ready
                        ? null
                        : _scan,
                    icon: const Icon(Icons.usb),
                    label: Text(_useRelay ? 'Select relay' : 'Scan USB'),
                  ),
                  FilledButton.icon(
                    onPressed: busy || _selected == null || session.ready
                        ? null
                        : () => session.connect(_selected!),
                    icon: const Icon(Icons.link),
                    label: const Text('Connect'),
                  ),
                  OutlinedButton(
                    onPressed: busy || !session.ready ? null : session.refresh,
                    child: const Text('Refresh status'),
                  ),
                  TextButton(
                    onPressed: busy || !session.ready
                        ? null
                        : session.disconnect,
                    child: const Text('Disconnect'),
                  ),
                ],
              ),
              const SizedBox(height: 20),
              if (busy) const LinearProgressIndicator(),
              const SizedBox(height: 12),
              Text(session.status),
              if (session.lastUpdate != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Snapshot: ${session.lastUpdate!.toLocal().toString().split('.').first} • refresh for current data',
                  ),
                ),
              const SizedBox(height: 16),
              ExpansionTile(
                key: const PageStorageKey('radio-details'),
                title: const Text('Radio details'),
                children: [
                  for (final entry in session.values.entries)
                    Card(
                      child: ListTile(
                        title: Text(entry.key),
                        trailing: Text(entry.value),
                      ),
                    ),
                ],
              ),
            ],
          ],
        ),
      ),
    );

    return Scaffold(
      appBar: AppBar(title: const Text('NIGHTFIRE')),
      body: SafeArea(
        child: Column(
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
              color: Theme.of(context).colorScheme.surfaceContainer,
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Wrap(
                    spacing: 24,
                    runSpacing: 8,
                    children: [
                      Text(
                        session?.ready == true
                            ? '● Radio connected'
                            : '○ Radio offline',
                        style: TextStyle(
                          color: session?.ready == true
                              ? Colors.lightGreen
                              : Colors.blueGrey.shade200,
                        ),
                      ),
                      Text(
                        session?.master.armSummary ?? 'ARM STATE UNKNOWN',
                        style: TextStyle(
                          color: Colors.amber,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      if (_useRelay)
                        const Text(
                          'Development relay',
                          style: TextStyle(color: Colors.amber),
                        ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Text(
                    session?.master.supported == true
                        ? 'Arm acknowledgements apply to this USB session. STOP / DISARM ALL is available in Modules, Fire and Scripts.'
                        : 'Remote arm state is unconfirmed. Arming and firing remain disabled.',
                  ),
                ],
              ),
            ),
            Expanded(
              child: ControllerNavigation(
                selectedIndex: _section,
                onSelected: (index) => setState(() => _section = index),
                pages: [
                  _page('modules', [
                    Text(
                      'Command module',
                      style: Theme.of(context).textTheme.headlineLarge,
                    ),
                    const SizedBox(height: 8),
                    const Text('Module and cue dashboard'),
                    if (session?.ready != true) ...[
                      const SizedBox(height: 16),
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(16),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Connect your radio to discover modules and check continuity.',
                              ),
                              const SizedBox(height: 8),
                              FilledButton.icon(
                                onPressed: () => setState(() => _section = kRadioSection),
                                icon: const Icon(Icons.usb),
                                label: const Text('Open radio connection'),
                              ),
                            ],
                          ),
                        ),
                      ),
                    ],
                    if (session != null) ...[
                      const SizedBox(height: 16),
                      Text(session.status),
                      MasterControlsPanel(
                        session: session,
                        onPickingFile: _pickingScriptChanged,
                      ),
                      FieldModulesPanel(
                        session: session,
                        script: _loadedScript,
                        focus: _moduleFocus,
                      ),
                    ],
                  ]),
                  _page('fire', [
                    Text(
                      'Manual fire',
                      style: Theme.of(context).textTheme.headlineLarge,
                    ),
                    const SizedBox(height: 8),
                    const Text('Select a module, arm, then tap a cue to fire'),
                    const SizedBox(height: 16),
                    if (session != null)
                      ManualFirePanel(session: session)
                    else
                      const Text(
                        'Manual fire requires a supported radio connection.',
                      ),
                  ]),
                  _page('scripts', [
                    ScriptsPanel(
                      session: session,
                      pickScript: widget.pickScript,
                      onPickingScriptChanged: _pickingScriptChanged,
                      onOpenRadio: () => setState(() => _section = kRadioSection),
                      onScriptChanged: (script) =>
                          setState(() => _loadedScript = script),
                      onOpenModule: (focus) => setState(() {
                        _moduleFocus = focus;
                        _section = 0;
                      }),
                    ),
                  ]),
                  connectionPage,
                  _page('interface', [
                    Text(
                      'Hardware status',
                      style: Theme.of(context).textTheme.headlineLarge,
                    ),
                    const SizedBox(height: 8),
                    const Text(
                      'Physical switch, status LEDs and host heartbeat',
                    ),
                    const SizedBox(height: 16),
                    if (session != null)
                      CommandInterfacePanel(session: session)
                    else
                      const Text(
                        'Hardware status requires a supported radio connection.',
                      ),
                    const SizedBox(height: 16),
                    const Text('Hardware wiring and bench checks are pending.'),
                  ]),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
