part of 'radio_session.dart';

/// Master operations share the diagnostic session's single correlated RF query.
/// No action is replayed automatically after a timeout or a reconnect.
class MasterController {
  MasterController(this.session);
  final RadioSession session;
  final armed = <int>{};
  final uploaded = <int, List<(int, BigInt)>>{};
  final _scriptStartSequence = <int, int>{};
  ScriptDocument? document;
  int? scriptId;
  bool running = false;
  bool stepping = false;
  bool interrupted = false;
  bool stopping = false;
  int stepIndex = 0;
  String status = 'Master bridge firmware is required for live controls.';
  Timer? _scriptTimer;
  final Stopwatch _clock = Stopwatch()..start();
  int _startMs = 0;
  bool _heartbeatBusy = false;
  int _lastFireMs = -180;

  /// Most recent field-confirmed manual fire, shown like the master's
  /// last-fired cue indicator.
  ({int uid, int cue})? lastFire;
  DateTime? lastFireAt;

  /// Cues sent per module since it was armed; cleared when it is disarmed.
  final firedCues = <int, Set<int>>{};

  void _clearFired([int? uid]) {
    if (uid == null) {
      firedCues.clear();
      lastFire = null;
    } else {
      firedCues.remove(uid);
      if (lastFire?.uid == uid) lastFire = null;
    }
  }
  int get ticks => _clock.elapsedMilliseconds & 0x3fffffff;
  int get elapsedMs => running ? _clock.elapsedMilliseconds - _startMs : 0;
  bool get supported =>
      session.ready && session.values['Firmware'] == 'usb-master-0.4.0';
  bool get available => supported && !session.busy && !interrupted && !stopping;
  String get armSummary => armed.isEmpty
      ? 'ARM STATE UNKNOWN'
      : '${armed.length} module(s): ARM acknowledged';

  void invalidate() {
    _scriptTimer?.cancel();
    _scriptTimer = null;
    armed.clear();
    _clearFired();
    uploaded.clear();
    _scriptStartSequence.clear();
    document = null;
    scriptId = null;
    running = false;
    stepping = false;
    interrupted = true;
    status = 'Session ended. Remote arm state is unknown.';
  }

  void _check(int epoch) {
    if (!supported || session._epoch != epoch || interrupted) {
      throw StateError('Master operation cancelled');
    }
  }

  Future<bool> _operation(
    String label,
    Future<void> Function(int) body, {
    bool recovery = false,
  }) async {
    if (!supported || session.busy || stopping || (interrupted && !recovery)) {
      return false;
    }
    final epoch = session._epoch;
    if (recovery) interrupted = false;
    session.busy = true;
    status = '$label…';
    session.notifyListeners();
    try {
      await body(epoch);
      _check(epoch);
      status = '$label complete';
      return true;
    } catch (error) {
      status = '$label unconfirmed: $error';
      return false;
    } finally {
      session.busy = false;
      session.notifyListeners();
    }
  }

  Future<void> _ack(
    int uid,
    int command,
    int epoch, [
    List<int> data = const [],
    List<int>? frame,
    Duration? timeout,
  ]) async {
    _check(epoch);
    final reply = await session._queryModule(
      uid,
      command,
      0x50,
      frame: frame ?? masterUnique(uid, command, data),
      timeout: timeout,
    );
    _check(epoch);
    if (reply.data.length != 2 ||
        reply.data[0] != command ||
        reply.data[1] != 0) {
      throw StateError('Module did not acknowledge command $command');
    }
  }

  Future<void> _broadcast(
    int command,
    int epoch, [
    List<int> data = const [],
  ]) async {
    _check(epoch);
    await session._request(0x10, masterBroadcast(command, data));
    _check(epoch);
  }

  /// Output status is a read-only query, so a lost RF reply is retried.
  /// Commands that actuate (ARM, FIRE, START, STEP) are never retried.
  static const statusAttempts = 3;

  Future<OutputStatus> _outputStatus(int uid, int epoch) async {
    for (var attempt = 1; ; attempt++) {
      try {
        return await _outputStatusOnce(uid, epoch);
      } on TimeoutException {
        if (attempt >= statusAttempts) rethrow;
      }
    }
  }

  Future<OutputStatus> _outputStatusOnce(int uid, int epoch) async {
    _check(epoch);
    final reply = await session._queryModule(
      uid,
      0x27,
      0x28,
      frame: moduleQuery(uid, 0x27),
    );
    _check(epoch);
    final result = OutputStatus.parse(reply.data);
    final module = session.modules[uid];
    if (module == null || result.cueCount != module.cueCount) {
      throw StateError('Output status cue count does not match discovery');
    }
    module.outputStatus = result;
    module.outputStatusAt = DateTime.now();
    return result;
  }

  Future<bool> refreshOutputStatus(int uid) =>
      _operation('Read output state', (epoch) async {
        await _outputStatus(uid, epoch);
      });

  Future<void> _armOne(int uid, int epoch) async {
    final module = session.modules[uid];
    if (module == null || !module.seenInScan || running || stepping) {
      throw StateError('Discover this module before arming');
    }
    armed.remove(uid);
    await _ack(uid, 0x10, epoch);
    final output = await _outputStatus(uid, epoch);
    if (!output.armed || !output.driverEnabled || output.startupFault) {
      throw StateError('Field output hardware did not confirm ARM');
    }
    armed.add(uid);
  }

  /// DISARM is safe to repeat, so a lost packet or ACK is retried (like
  /// master_main.py's send_with_ack), unlike ARM/FIRE/START/STEP.
  static const disarmAttempts = 3;
  static const disarmAckTimeout = Duration(milliseconds: 1500);

  Future<void> _disarmOne(int uid, int epoch) async {
    armed.remove(uid);
    _clearFired(uid);
    for (var attempt = 1; ; attempt++) {
      try {
        await _ack(uid, 0x11, epoch, const [], null, disarmAckTimeout);
        break;
      } on TimeoutException {
        if (attempt >= disarmAttempts) rethrow;
      }
    }
    final output = await _outputStatus(uid, epoch);
    if (output.armed ||
        output.driverEnabled ||
        output.activeMask != BigInt.zero) {
      throw StateError('Field output hardware did not confirm DISARM');
    }
  }

  /// Runs [action] on every module, continuing past per-module failures the
  /// way master_main.py's set_armed() does, then reports the failure count.
  Future<void> _forEachModule(
    Iterable<int> uids,
    int epoch,
    String verb,
    Future<void> Function(int uid, int epoch) action,
  ) async {
    var failed = 0;
    for (final uid in uids.toList()) {
      try {
        await action(uid, epoch);
      } catch (_) {
        // A cancelled session must abort the whole sweep, not count a failure.
        _check(epoch);
        failed++;
      }
      session.notifyListeners();
    }
    if (failed > 0) {
      throw StateError('$verb not confirmed for $failed module(s)');
    }
  }

  Future<bool> armModule(int uid) =>
      _operation('Arm module', (epoch) => _armOne(uid, epoch));

  /// Arms every discovered module (the master's single ARM toggle).
  Future<bool> armAll() => _operation('Arm all modules', (epoch) async {
    final targets = session.modules.values
        .where((m) => m.seenInScan)
        .map((m) => m.uid);
    if (targets.isEmpty || running || stepping) {
      throw StateError('Arm disabled - scan modules first');
    }
    await _forEachModule(targets, epoch, 'ARM', _armOne);
  });

  /// Disarms every known module with a per-module acknowledgement.
  Future<bool> disarmAll() => _operation('Disarm all modules', (epoch) async {
    final targets = {
      ...armed,
      ...session.modules.values.where((m) => m.seenInScan).map((m) => m.uid),
    };
    if (targets.isEmpty) throw StateError('No modules to disarm');
    // Broadcast first so every module disarms even if its unique reply is
    // lost (master_main.py's disarm_repeated), then confirm each module.
    for (var i = 0; i < 3; i++) {
      await _broadcast(0x11, epoch);
    }
    await _forEachModule(targets, epoch, 'DISARM', _disarmOne);
  }, recovery: true);

  /// Stop/disarm interrupts uploads, queries and pending actuation immediately.
  /// Broadcast acceptance is never presented as a remote acknowledgement.
  Future<void> emergencyStop() async {
    if (stopping) return;
    if (!supported) {
      invalidate();
      session.notifyListeners();
      return;
    }
    interrupted = true;
    stopping = true;
    _scriptTimer?.cancel();
    running = false;
    stepping = false;
    armed.clear();
    _clearFired();
    final pending = session._modulePending;
    if (pending != null && !pending.isCompleted) {
      pending.completeError(StateError('Stopped by operator'));
    }
    status = 'Sending STOP and DISARM; remote state unconfirmed';
    session.notifyListeners();
    Object? failure;
    for (var i = 0; i < 3 && supported; i++) {
      for (final frame in [
        masterBroadcast(0x73, [0, 0]),
        masterBroadcast(0x11),
      ]) {
        try {
          await session._request(0x10, frame);
        } catch (error) {
          failure = error;
        }
      }
    }
    status = failure == null
        ? 'STOP/DISARM transmitted; confirm disarm per module'
        : 'STOP/DISARM transmission incomplete: $failure';
    stopping = false;
    session.notifyListeners();
  }

  Future<bool> disarmModule(int uid) => _operation(
    'Disarm module',
    (epoch) => _disarmOne(uid, epoch),
    recovery: true,
  );

  /// Manual fire matches master_main.py's fire_unique(): one FIRE packet is
  /// transmitted and nothing waits on the module. The bridge's reply only
  /// means the packet went out over RF. FIRE is never retried.
  Future<bool> fire(int uid, int cue) =>
      _operation('Send fire cue $cue', (epoch) async {
        final module = session.modules[uid];
        if (!armed.contains(uid) ||
            module == null ||
            cue < 1 ||
            cue > module.cueCount ||
            running ||
            stepping) {
          throw StateError('Arm the selected module before firing a valid cue');
        }
        if (_clock.elapsedMilliseconds - _lastFireMs < 180) {
          throw StateError('Manual fire debounce is active');
        }
        _lastFireMs = _clock.elapsedMilliseconds;
        _check(epoch);
        await session._request(
          0x10,
          masterUnique(uid, 0x20, maskBytes(BigInt.one << (cue - 1))),
        );
        _check(epoch);
        lastFire = (uid: uid, cue: cue);
        lastFireAt = DateTime.now();
        firedCues.putIfAbsent(uid, () => {}).add(cue);
      });

  void _requireDisarmed() {
    if (armed.isNotEmpty || running || stepping) {
      throw StateError('Disarm before maintenance or script upload');
    }
  }

  Future<bool> ping(
    int uid, {
    int count = 1,
  }) => _operation('Range/speed test', (epoch) async {
    _requireDisarmed();
    var received = 0;
    var total = 0;
    for (var i = 0; i < count.clamp(1, 100); i++) {
      _check(epoch);
      final watch = Stopwatch()..start();
      try {
        final token = bigEndian(ticks, 4);
        final reply = await session._queryModule(
          uid,
          0x7f,
          0x7f,
          frame: masterUnique(uid, 0x7f, token),
        );
        _check(epoch);
        if (!listEquals(reply.data, token)) {
          throw StateError('Ping token mismatch');
        }
        received++;
        total += watch.elapsedMilliseconds;
      } on TimeoutException {
        _check(epoch);
      }
      status =
          'Ping $received/${i + 1}, mean ${received == 0 ? "--" : total ~/ received} ms';
      session.notifyListeners();
    }
    session.values['Range test'] = status;
  });

  Future<bool> setModuleValue(int uid, int command, int value) =>
      _operation('Save module setting', (epoch) async {
        _requireDisarmed();
        final size = switch (command) {
          0x30 => 4,
          0x32 => 2,
          0x33 => 1,
          _ => throw ArgumentError('Unsupported setting'),
        };
        if (value < 0 ||
            value > (size == 4 ? 0xffffffff : (1 << (size * 8)) - 1) ||
            (command == 0x33 && value != 0 && (value < 74 || value > 100))) {
          throw ArgumentError('Invalid setting value');
        }
        await _ack(uid, command, epoch, bigEndian(value, size));
        session.modules[uid]?.seenInScan = false;
        uploaded.clear();
        document = null;
        session.values['Module setting'] =
            'Acknowledged. Rediscover on the new network/channel/UID.';
      });

  Future<bool> rawResistance(int uid) => _operation('Read raw ADC', (
    epoch,
  ) async {
    _requireDisarmed();
    final reply = await session._queryModule(
      uid,
      0x24,
      0x25,
      frame: masterUnique(uid, 0x24, [1]),
    );
    _check(epoch);
    final module = session.modules[uid]!;
    module.resistance = ResistanceSnapshot.parse(reply.data, module.cueCount);
    module.measuredAt = DateTime.now();
  });

  Future<bool> calibrate(int uid, int bank, List<(int, int)> points) =>
      _operation('Save resistance calibration', (epoch) async {
        _requireDisarmed();
        if (bank < 0 ||
            bank >= (session.modules[uid]!.cueCount + 7) ~/ 8 ||
            points.isEmpty ||
            points.length > 8 ||
            points.any(
              (p) => p.$1 < 0 || p.$1 > 65535 || p.$2 < 0 || p.$2 > 65535,
            )) {
          throw ArgumentError(
            'Use up to eight ADC mV / deci-ohm pairs in a valid bank',
          );
        }
        await _ack(uid, 0x26, epoch, [
          1,
          bank,
          points.length,
          for (final point in points) ...[
            ...bigEndian(point.$1, 2),
            ...bigEndian(point.$2, 2),
          ],
        ]);
        session.modules[uid]!.resistance = null;
      });

  Future<bool> uploadScript(
    ScriptDocument script,
  ) => _operation('Upload script', (epoch) async {
    _requireDisarmed();
    if (script.findings.any((f) => f.error) ||
        script.events.isEmpty ||
        script.auxiliaryCount != 0) {
      throw StateError('Fix script errors before upload');
    }
    final plan = <int, Map<int, BigInt>>{};
    for (final event in script.events) {
      final matches = session.modules.values
          .where((m) => m.group == event.channel)
          .toList();
      if (matches.length != 1 ||
          !matches.single.seenInScan ||
          event.cue > matches.single.cueCount) {
        throw StateError(
          'Group ${event.channel} must identify one discovered module with cue ${event.cue}',
        );
      }
      final events = plan.putIfAbsent(matches.single.uid, () => {});
      events[event.timeMs] =
          (events[event.timeMs] ?? BigInt.zero) |
          (BigInt.one << (event.cue - 1));
    }
    if (plan.values.any((events) => events.length > 192)) {
      throw StateError('Field firmware permits 192 events per module');
    }
    uploaded.clear();
    _scriptStartSequence.clear();
    document = null;
    scriptId = (ticks % 65535) + 1;
    try {
      for (final module in plan.entries) {
        final entries =
            module.value.entries.map((e) => (e.key, e.value)).toList()
              ..sort((a, b) => a.$1.compareTo(b.$1));
        await _ack(module.key, 0x70, epoch, bigEndian(scriptId!, 2));
        for (var offset = 0; offset < entries.length; offset += 3) {
          final end = (offset + 3).clamp(0, entries.length);
          // The field appends LOAD chunks without deduplication: never retry.
          await _ack(
            module.key,
            0x71,
            epoch,
            [],
            scriptChunk(
              module.key,
              scriptId!,
              offset ~/ 3,
              entries.sublist(offset, end),
            ),
          );
          status =
              'Upload ${session.modules[module.key]!.id}: $end/${entries.length}';
          session.notifyListeners();
        }
        uploaded[module.key] = entries;
        final info = moduleInfo(
          (await session._queryModule(module.key, 0x52, 0x53)).data,
        );
        _check(epoch);
        final reported = info['Script'] ?? '';
        final idText = scriptId!.toRadixString(16).padLeft(4, '0');
        final fields = reported.toLowerCase().split(RegExp(r'\s+'));
        if (!fields.contains('id=$idText') ||
            !fields.contains('entries=${entries.length}')) {
          throw StateError(
            'Module script readback does not match uploaded ID/event count',
          );
        }
        session.modules[module.key]!.info = info;
      }
      document = script;
      stepIndex = 0;
    } catch (_) {
      uploaded.clear();
      scriptId = null;
      rethrow;
    }
  });

  Future<bool> clearScript() => _operation('Clear script', (epoch) async {
    _requireDisarmed();
    final targets = uploaded.keys.toList();
    document = null;
    uploaded.clear();
    for (final uid in targets) {
      await _ack(uid, 0x70, epoch, [0, 0]);
    }
    scriptId = null;
  });

  List<int> get eventTimes =>
      uploaded.values.expand((e) => e.map((e) => e.$1)).toSet().toList()
        ..sort();

  Future<bool> startScript({bool step = false, int offsetMs = 0}) =>
      _operation(step ? 'Prepare step mode' : 'Start script', (epoch) async {
        if (document == null ||
            uploaded.isEmpty ||
            running ||
            stepping ||
            uploaded.keys.any((uid) => !armed.contains(uid)) ||
            offsetMs < 0 ||
            offsetMs > eventTimes.last) {
          throw StateError('Upload and arm every script module first');
        }
        stepIndex = eventTimes.indexWhere((time) => time >= offsetMs);
        if (step) {
          stepping = true;
          return;
        }
        for (var i = 0; i < 3; i++) {
          await _broadcast(0x75, epoch, bigEndian(ticks, 4));
        }
        // All modules receive one shared absolute start, allowing ACK latency.
        final delay = 2000 + uploaded.length * 4000;
        if (delay > 60000) {
          throw StateError('Too many modules for a synchronized start');
        }
        final start = (ticks + delay) & 0x3fffffff;
        _startMs = _clock.elapsedMilliseconds + delay - offsetMs;
        running = true;
        _scriptTimer = Timer.periodic(
          const Duration(milliseconds: 200),
          (_) => unawaited(_scriptHeartbeat(epoch)),
        );
        try {
          for (final uid in uploaded.keys) {
            final before = await _outputStatus(uid, epoch);
            _scriptStartSequence[uid] = before.fireSequence;
            await _broadcast(0x75, epoch, bigEndian(ticks, 4));
            if (_clock.elapsedMilliseconds >= _startMs + offsetMs - 1000) {
              throw StateError('Start deadline missed; script stopped');
            }
            await _ack(uid, 0x72, epoch, [
              ...bigEndian(scriptId!, 2),
              ...bigEndian(delay, 2),
              ...bigEndian(offsetMs, 4),
              ...bigEndian(start, 4),
            ]);
            final started = await _outputStatus(uid, epoch);
            if (!started.armed ||
                !started.driverEnabled ||
                !started.scriptRunning) {
              throw StateError('Field module did not confirm script start');
            }
          }
        } catch (_) {
          await emergencyStop();
          rethrow;
        }
      });

  Future<void> _scriptHeartbeat(int epoch) async {
    if (_heartbeatBusy || !running) return;
    _heartbeatBusy = true;
    try {
      await _broadcast(0x74, epoch, [
        ...bigEndian(scriptId!, 2),
        ...bigEndian(elapsedMs.clamp(0, 0xffffffff), 4),
      ]);
      if (elapsedMs > eventTimes.last + 1000) {
        for (final module in uploaded.entries) {
          final output = await _outputStatus(module.key, epoch);
          final expected =
              ((_scriptStartSequence[module.key] ?? output.fireSequence) +
                  module.value.length) &
              0xffffffff;
          if (output.fireSequence != expected ||
              output.lastFireMask != module.value.last.$2) {
            throw StateError(
              '${session.modules[module.key]!.id} did not confirm every script output',
            );
          }
        }
        await emergencyStop();
        status = 'Script outputs confirmed; STOP/DISARM sent';
      }
      session.notifyListeners();
    } catch (error) {
      if (supported) await emergencyStop();
      invalidate();
      status = 'Script heartbeat failed: $error';
      session.notifyListeners();
    } finally {
      _heartbeatBusy = false;
    }
  }

  Future<bool> nextStep() => _operation('Step event', (epoch) async {
    final times = eventTimes;
    if (!stepping ||
        stepIndex >= times.length ||
        uploaded.keys.any((uid) => !armed.contains(uid))) {
      throw StateError('Step mode is not ready');
    }
    // Consume before send: a partial transmission must never replay this event.
    final time = times[stepIndex++];
    try {
      for (final module in uploaded.entries.where(
        (m) => m.value.any((e) => e.$1 == time),
      )) {
        _check(epoch);
        final before = await _outputStatus(module.key, epoch);
        final expectedMask = module.value
            .where((entry) => entry.$1 == time)
            .fold(BigInt.zero, (mask, entry) => mask | entry.$2);
        await session._request(
          0x10,
          masterUnique(module.key, 0x76, [
            ...bigEndian(scriptId!, 2),
            ...bigEndian(time, 4),
          ]),
        );
        final after = await _outputStatus(module.key, epoch);
        if (after.fireSequence != ((before.fireSequence + 1) & 0xffffffff) ||
            after.lastFireMask != expectedMask) {
          throw StateError('Field output did not confirm script step');
        }
      }
      session.values['Last script step'] = '$time ms output confirmed';
    } catch (_) {
      await emergencyStop();
      rethrow;
    }
  });

  Future<bool> updateModule(int uid, String path, List<int> bytes) =>
      _operation('Update module file', (epoch) async {
        _requireDisarmed();
        if (bytes.isEmpty || bytes.length > 256 * 1024) {
          throw ArgumentError('Module files must be 1 byte to 256 KiB');
        }
        final transfer = ticks & 65535;
        final begin = otaBegin(uid, transfer, path, bytes);
        await _ack(uid, 0x60, epoch, [], begin);
        try {
          // 80 bytes fits even a worst-case escaped NFCP frame in the USB MTU.
          for (var offset = 0; offset < bytes.length; offset += 80) {
            final end = (offset + 80).clamp(0, bytes.length);
            await _ack(uid, 0x61, epoch, [
              ...bigEndian(transfer, 2),
              ...bigEndian(offset, 4),
              ...bytes.sublist(offset, end),
            ]);
            status = 'Module update: $end/${bytes.length} bytes';
            session.notifyListeners();
          }
          await _ack(uid, 0x62, epoch, bigEndian(transfer, 2));
        } catch (_) {
          if (supported) {
            try {
              await session._request(
                0x10,
                masterUnique(uid, 0x63, bigEndian(transfer, 2)),
              );
            } catch (_) {
              /* Preserve original transfer error. */
            }
          }
          rethrow;
        }
      });

  Future<bool> rebootModule(int uid) =>
      _operation('Reboot module', (epoch) async {
        _requireDisarmed();
        await _ack(uid, 0x64, epoch);
        session.modules[uid]?.seenInScan = false;
        uploaded.clear();
        document = null;
      });

  Future<bool> updateBridge(String name, List<int> bytes) =>
      _operation('Update bridge file', (epoch) async {
        _requireDisarmed();
        if (![
              'main.py',
              'bridge_radio.py',
              'nrf_bridge_config.py',
              'usb_master_control.py',
              'usb_master_maintenance.py',
              'usb_bridge_transport.py',
              'usb_monitor_nfcp.py',
              'command_interface.py',
            ].contains(name) ||
            bytes.isEmpty ||
            bytes.length > 256 * 1024) {
          throw ArgumentError('Unsupported bridge filename or file size');
        }
        final filename = name.codeUnits;
        await session._request(0x32, [
          filename.length,
          ...filename,
          ...bigEndian(bytes.length, 4),
          ...bigEndian(crc16(bytes), 2),
        ]);
        for (var offset = 0; offset < bytes.length; offset += 48) {
          _check(epoch);
          final end = (offset + 48).clamp(0, bytes.length);
          await session._request(0x33, [
            ...bigEndian(offset, 4),
            ...bytes.sublist(offset, end),
          ]);
          status = 'Bridge update: $end/${bytes.length} bytes';
          session.notifyListeners();
        }
        _check(epoch);
        await session._request(0x34);
      });

  Future<bool> bridgeCommand(int command, [List<int> payload = const []]) =>
      _operation('Radio operation', (epoch) async {
        _requireDisarmed();
        if (![0x12, 0x13, 0x14, 0x16, 0x31].contains(command)) {
          throw ArgumentError('Unsupported radio operation');
        }
        final reply = await session._request(command, payload);
        _check(epoch);
        if (command == 0x14) {
          session.values['Channel scan'] = parseChannelScan(reply.payload);
        } else if (command == 0x31) {
          await session.disconnect();
          return;
        } else {
          session.values = {
            ...session.values,
            ...bridgeInfo((await session._request(3)).payload),
            ...bridgeStats((await session._request(0x15)).payload),
          };
          if (command == 0x12 || command == 0x13) {
            session.modules.clear();
            uploaded.clear();
            document = null;
          }
        }
      });
}

String parseChannelScan(List<int> data) {
  if (data.isEmpty || data.length != 1 + data[0] * 10) {
    throw const FormatException('Invalid channel scan');
  }
  int signed(int offset) {
    final value = readUnsigned(data, offset, 2);
    return value >= 32768 ? value - 65536 : value;
  }

  return [
    for (var i = 1; i < data.length; i += 10)
      'CH ${data[i]}: avg ${signed(i + 1)} dBm, peak ${signed(i + 5)} dBm, score ${data[i + 9]}',
  ].join('\n');
}
