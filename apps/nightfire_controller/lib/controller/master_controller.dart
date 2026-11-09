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

  /// Script time a paused run resumes from; null unless paused.
  int? pausedAtMs;

  /// Events a pause found unfired before its resume point; they are skipped.
  int skippedOnPause = 0;

  /// True once a run fired its last event and was confirmed.
  bool completed = false;
  int _runOffsetMs = 0;
  bool get paused => pausedAtMs != null;
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
      session.ready && isMasterFirmware(session.values['Firmware']);
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
    pausedAtMs = null;
    completed = false;
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
    session.busy = true;
    final epoch = session._epoch;
    if (recovery) interrupted = false;
    status = '$label…';
    session.notifyListeners();
    try {
      // A background continuity check may hold the module reply slot.
      await session.waitForAutoContinuity();
      _check(epoch);
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
  Future<bool> disarmAll() =>
      _operation('Disarm all modules', _disarmEverything, recovery: true);

  Future<void> _disarmEverything(int epoch) async {
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
  }

  /// A finished run leaves no module armed: every module is disarmed and
  /// each one confirms it, as Disarm does.
  Future<void> _finishRun() async {
    _scriptTimer?.cancel();
    _scriptTimer = null;
    running = false;
    pausedAtMs = null;
    completed = true;
    if (await disarmAll()) {
      status = 'Script complete; all modules disarmed';
    } else {
      await emergencyStop();
      status = 'Script complete; DISARM not confirmed for every module. Press Disarm.';
    }
    session.notifyListeners();
  }

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
    pausedAtMs = null;
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

  /// Arms only the modules that hold the uploaded script.
  Future<bool> armScriptModules() =>
      _operation('Arm script modules', (epoch) async {
        if (uploaded.isEmpty) {
          throw StateError('Send the script to modules first');
        }
        await _forEachModule(uploaded.keys, epoch, 'ARM', _armOne);
      });

  /// Operator STOP ALL followed by a confirmed per-module disarm.
  Future<bool> stopAllAndDisarm() async {
    await emergencyStop();
    return disarmAll();
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

  /// Lead time before a synchronized start: a base plus a share per module.
  int startLeadMs = 2000;
  int startLeadPerModuleMs = 4000;

  /// Whole-module upload attempts (each restarts with CLEAR).
  static const uploadAttempts = 3;

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
    pausedAtMs = null;
    completed = false;
    scriptId = (ticks % 65535) + 1;
    try {
      for (final module in plan.entries) {
        final entries =
            module.value.entries.map((e) => (e.key, e.value)).toList()
              ..sort((a, b) => a.$1.compareTo(b.$1));
        late Map<String, String> info;
        for (var attempt = 1; ; attempt++) {
          try {
            // CLEAR empties the module's list, so a lost reply restarts the
            // whole module from here. A LOAD chunk is never resent on its
            // own: the field appends chunks without deduplication.
            await _ack(module.key, 0x70, epoch, bigEndian(scriptId!, 2));
            await Future<void>.delayed(updateSettle);
            for (var offset = 0; offset < entries.length; offset += 3) {
              final end = (offset + 3).clamp(0, entries.length);
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
              // Let the field finish repeating its ACK before the next frame.
              await Future<void>.delayed(updateSettle);
              status =
                  'Upload ${session.modules[module.key]!.id}: $end/${entries.length}';
              session.notifyListeners();
            }
            info = moduleInfo(
              (await session._queryModule(module.key, 0x52, 0x53)).data,
            );
            break;
          } on TimeoutException {
            _check(epoch);
            if (attempt >= uploadAttempts) rethrow;
            status =
                'Upload ${session.modules[module.key]!.id}: reply lost, '
                'restarting (try ${attempt + 1}/$uploadAttempts)';
            session.notifyListeners();
            await Future<void>.delayed(updateSettle);
          }
        }
        uploaded[module.key] = entries;
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
    pausedAtMs = null;
    completed = false;
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
        completed = false;
        if (step) {
          pausedAtMs = null;
          stepping = true;
          return;
        }
        _runOffsetMs = offsetMs;
        for (var i = 0; i < 3; i++) {
          await _broadcast(0x75, epoch, bigEndian(ticks, 4));
        }
        // All modules receive one shared absolute start, allowing ACK latency.
        final delay = startLeadMs + uploaded.length * startLeadPerModuleMs;
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
          pausedAtMs = null;
          skippedOnPause = 0;
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
          // A run started at an offset only fires the events from there on.
          final remaining = module.value
              .where((entry) => entry.$1 >= _runOffsetMs)
              .length;
          final expected =
              ((_scriptStartSequence[module.key] ?? output.fireSequence) +
                  remaining) &
              0xffffffff;
          if (output.fireSequence != expected ||
              (remaining > 0 && output.lastFireMask != module.value.last.$2)) {
            throw StateError(
              '${session.modules[module.key]!.id} did not confirm every script output',
            );
          }
        }
        await _finishRun();
        return;
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

  /// Stops every script module's scheduler while leaving it armed: STOP is
  /// broadcast first so all modules halt together, then each one confirms.
  Future<void> _haltScript(int epoch) async {
    _scriptTimer?.cancel();
    _scriptTimer = null;
    while (_heartbeatBusy) {
      await Future<void>.delayed(const Duration(milliseconds: 10));
    }
    try {
      final stop = bigEndian(scriptId!, 2);
      for (var i = 0; i < 3; i++) {
        await _broadcast(0x73, epoch, stop);
      }
      for (final uid in uploaded.keys) {
        await _ack(uid, 0x73, epoch, stop);
      }
    } catch (_) {
      await emergencyStop();
      rethrow;
    }
  }

  /// Pauses a timed run. Each module's fire counter says exactly which events
  /// it fired, so the resume point always lies after every fired event: a
  /// resumed run never fires an event twice.
  Future<bool> pauseScript() => _operation('Pause script', (epoch) async {
    if (!running) throw StateError('No script is running');
    await _haltScript(epoch);
    final stoppedAt = elapsedMs;
    running = false;
    var after = _runOffsetMs; // resume no earlier than this
    var before = 1 << 31; // nor later than the first unfired event
    final unfired = <int>[];
    try {
      for (final module in uploaded.entries) {
        final output = await _outputStatus(module.key, epoch);
        final events = module.value;
        var first = events.indexWhere((entry) => entry.$1 >= _runOffsetMs);
        if (first < 0) first = events.length;
        final fired =
            (output.fireSequence -
                (_scriptStartSequence[module.key] ?? output.fireSequence)) &
            0xffffffff;
        final next = first + fired;
        if (next > events.length) {
          throw StateError(
            '${session.modules[module.key]!.id} reported more outputs than scheduled',
          );
        }
        if (next > first) after = math.max(after, events[next - 1].$1 + 1);
        if (next < events.length) before = math.min(before, events[next].$1);
        unfired.addAll(events.skip(next).map((entry) => entry.$1));
      }
    } catch (_) {
      await emergencyStop();
      rethrow;
    }
    final resume = after <= before ? stoppedAt.clamp(after, before) : after;
    skippedOnPause = unfired.where((time) => time < resume).length;
    if (unfired.every((time) => time < resume)) {
      // Nothing left to resume: the run is over, so disarm as on completion.
      completed = true;
      pausedAtMs = null;
      try {
        await _disarmEverything(epoch);
      } catch (_) {
        await emergencyStop();
        rethrow;
      }
      return;
    }
    pausedAtMs = resume;
  });

  /// Ends the run (timed, paused or stepped) without disarming; Start then
  /// begins again from the first event.
  Future<bool> stopScript() => _operation('Stop script', (epoch) async {
    if (!running && !paused && !stepping) {
      throw StateError('No script is running');
    }
    if (running) await _haltScript(epoch);
    running = false;
    stepping = false;
    pausedAtMs = null;
    skippedOnPause = 0;
  });

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

  // ---------------------------------------------------------------- Updates

  /// Field OTA chunks. 80 bytes fits even a worst-case escaped NFCP frame in
  /// the USB MTU. The field ACKs a repeated chunk without rewriting it, so a
  /// lost RF reply is retried; the field drops a transfer idle for 8 s, which
  /// [updateAttempts] × [updateAckTimeout] stays inside.
  static const updateChunk = 80;
  static const updateAttempts = 5;

  /// Realignments allowed per file after an out-of-step WRITE, and how far
  /// each one rewinds.
  static const maxResyncs = 20;
  static const resyncChunks = 4;
  Duration updateAckTimeout = const Duration(milliseconds: 1200);

  /// Wait between attempts to reopen the radio after it restarts.
  Duration reconnectDelay = const Duration(seconds: 2);

  /// The field module sends every reply three times, 20 ms apart, and cannot
  /// receive meanwhile. A chunk sent straight after the first ACK copy is lost
  /// and a later copy is taken as its ACK, so wait for the repeats to finish.
  Duration updateSettle = const Duration(milliseconds: 100);

  /// Time a field module needs to restart before it answers INFO.
  Duration rebootDelay = const Duration(seconds: 3);

  /// Transfer progress 0–1 for the Firmware page; null when no update runs.
  double? updateProgress;
  bool _cancelUpdate = false;

  /// Outcome per module UID of the last field firmware update.
  final firmwareResults = <int, String>{};

  /// Outcome of the last radio bridge update; survives the radio's restart.
  String? bridgeUpdateResult;

  void cancelUpdate() {
    _cancelUpdate = true;
    session.notifyListeners();
  }

  void _checkUpdate(int epoch) {
    _check(epoch);
    if (_cancelUpdate) throw StateError('Update cancelled');
  }

  Future<void> _ackWithRetry(
    int uid,
    int command,
    int epoch,
    List<int> data, [
    List<int>? frame,
  ]) async {
    for (var attempt = 1; ; attempt++) {
      _checkUpdate(epoch);
      try {
        await _ack(uid, command, epoch, data, frame, updateAckTimeout);
        await Future<void>.delayed(updateSettle);
        return;
      } on TimeoutException {
        if (attempt >= updateAttempts) rethrow;
      }
    }
  }

  Future<void> _sendModuleFile(
    int uid,
    String path,
    List<int> bytes,
    int epoch,
    void Function(int sent) progress,
  ) async {
    if (bytes.isEmpty || bytes.length > 256 * 1024) {
      throw ArgumentError('Module files must be 1 byte to 256 KiB');
    }
    final transfer = ticks & 65535;
    final begin = otaBegin(uid, transfer, path, bytes);
    await _ackWithRetry(uid, 0x60, epoch, const [], begin);
    try {
      var offset = 0;
      var resyncs = 0;
      while (offset < bytes.length) {
        final end = (offset + updateChunk).clamp(0, bytes.length);
        try {
          await _ackWithRetry(uid, 0x61, epoch, [
            ...bigEndian(transfer, 2),
            ...bigEndian(offset, 4),
            ...bytes.sublist(offset, end),
          ]);
        } on ModuleRejection catch (rejection) {
          // WRITE ACKs carry no offset. A late ACK for a re-sent chunk can be
          // taken as the next chunk's ACK, leaving the module behind; it then
          // rejects the gap (code 3). It only writes at its exact offset and
          // re-ACKs chunks it already has, so rewind and resend to realign.
          if (rejection.code != 3 || offset == 0 || ++resyncs > maxResyncs) {
            rethrow;
          }
          offset = (offset - resyncChunks * updateChunk).clamp(0, offset);
          continue;
        }
        progress(end);
        offset = end;
      }
      // END checks size and checksum before the field replaces the file.
      var endReplyLost = false;
      for (var attempt = 1; ; attempt++) {
        _checkUpdate(epoch);
        try {
          await _ack(
            uid,
            0x62,
            epoch,
            bigEndian(transfer, 2),
            null,
            updateAckTimeout,
          );
          break;
        } on TimeoutException {
          endReplyLost = true;
          if (attempt >= updateAttempts) rethrow;
        } on StateError {
          _checkUpdate(epoch);
          // A finished transfer rejects a repeated END: the first END was
          // applied and only its ACK was lost. The restart check confirms.
          if (!endReplyLost) rethrow;
          break;
        }
      }
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
  }

  Future<bool> updateModule(int uid, String path, List<int> bytes) =>
      _operation('Update module file', (epoch) async {
        _requireDisarmed();
        _cancelUpdate = false;
        await _sendModuleFile(uid, path, bytes, epoch, (sent) {
          status = 'Module update: $sent/${bytes.length} bytes';
          session.notifyListeners();
        });
      });

  /// Sends [bytes] as [path] to each module, reboots it and confirms it comes
  /// back as field firmware with its outputs disarmed. A failed module does
  /// not stop the others; cancelling or losing the radio does.
  Future<bool> updateModuleFirmware(
    List<int> uids,
    String path,
    List<int> bytes,
  ) => _operation('Field firmware update', (epoch) async {
    _requireDisarmed();
    if (uids.isEmpty) throw ArgumentError('Select at least one module');
    otaBegin(uids.first, 0, path, bytes); // Validate the path up front.
    _cancelUpdate = false;
    firmwareResults.removeWhere((uid, _) => uids.contains(uid));
    var failed = 0;
    try {
      for (var index = 0; index < uids.length; index++) {
        final uid = uids[index];
        final module = session.modules[uid];
        final name = module == null
            ? '0x${uid.toRadixString(16)}'
            : 'group ${module.group}';
        final before = module?.info['Firmware'];
        try {
          await _sendModuleFile(uid, path, bytes, epoch, (sent) {
            final percent = sent * 100 ~/ bytes.length;
            updateProgress = (index + sent / bytes.length) / uids.length;
            status = 'Updating $name (${index + 1}/${uids.length}): $percent%';
            firmwareResults[uid] = 'Sending $percent%';
            session.notifyListeners();
          });
          firmwareResults[uid] = 'Stored; restarting…';
          status = 'Restarting $name…';
          session.notifyListeners();
          final after = await _rebootAndVerify(uid, epoch);
          firmwareResults[uid] = before == null || before == after
              ? 'Installed • firmware $after'
              : 'Updated • $before → $after';
        } catch (error) {
          firmwareResults[uid] = 'Failed: $error';
          if (_cancelUpdate || session._epoch != epoch || !supported) rethrow;
          failed++;
        }
        session.notifyListeners();
      }
    } finally {
      updateProgress = null;
    }
    if (failed > 0) {
      throw StateError('$failed of ${uids.length} module(s) not updated');
    }
  });

  Future<String> _rebootAndVerify(int uid, int epoch) async {
    try {
      await _ack(uid, 0x64, epoch, const [], null, updateAckTimeout);
    } on TimeoutException {
      // The module resets right after its ACK, which can beat the RF reply.
    }
    session.modules[uid]?.seenInScan = false;
    uploaded.clear(); // A restarted module has lost any uploaded script.
    document = null;
    await Future<void>.delayed(rebootDelay);
    late Map<String, String> info;
    for (var attempt = 1; ; attempt++) {
      _check(epoch);
      try {
        final reply = await session._queryModule(
          uid,
          0x52,
          0x53,
          timeout: updateAckTimeout * 2,
        );
        info = moduleInfo(reply.data);
        break;
      } on TimeoutException {
        if (attempt >= updateAttempts) {
          throw StateError('Module did not answer after restarting');
        }
      }
    }
    if (info['App'] != 'field-module') {
      throw StateError('Restarted module reports "${info['App']}"');
    }
    final module = session.modules[uid];
    module?.info = info;
    module?.seenInScan = true; // It answered by unique ID after the restart.
    if ((await _outputStatus(uid, epoch)).armed) {
      throw StateError('Module restarted ARMED');
    }
    return info['Firmware'] ?? 'unknown';
  }

  Future<void> _sendBridgeFile(
    String name,
    List<int> bytes,
    int epoch,
    void Function(int sent) progress,
  ) async {
    final filename = name.codeUnits;
    await session._request(0x32, [
      filename.length,
      ...filename,
      ...bigEndian(bytes.length, 4),
      ...bigEndian(crc16(bytes), 2),
    ]);
    for (var offset = 0; offset < bytes.length; offset += 48) {
      final end = (offset + 48).clamp(0, bytes.length);
      for (var attempt = 1; ; attempt++) {
        _checkUpdate(epoch);
        try {
          // Bridge 0.4.1 ACKs a repeated chunk without writing it again.
          await session._request(0x33, [
            ...bigEndian(offset, 4),
            ...bytes.sublist(offset, end),
          ]);
          break;
        } on TimeoutException {
          if (attempt >= 3) rethrow;
        }
      }
      progress(end);
    }
    _checkUpdate(epoch);
    // END verifies length and CRC before the bridge replaces the file.
    await session._request(0x34);
  }

  Future<bool> updateBridge(String name, List<int> bytes) =>
      _operation('Update bridge file', (epoch) async {
        _requireDisarmed();
        if (!(bridgeFirmwareFiles.contains(name) ||
                name == 'nrf_bridge_config.py') ||
            bytes.isEmpty ||
            bytes.length > 256 * 1024) {
          throw ArgumentError('Unsupported bridge filename or file size');
        }
        _cancelUpdate = false;
        await _sendBridgeFile(name, bytes, epoch, (sent) {
          status = 'Bridge update: $sent/${bytes.length} bytes';
          session.notifyListeners();
        });
      });

  /// Installs bridge firmware files, restarts the radio and reconnects.
  /// The radio keeps a new bundle only once this app opens a session with
  /// it; otherwise it restores the previous bundle at its next start.
  Future<bool> updateBridgeFirmware(Map<String, List<int>> files) async {
    final unknown = files.keys.where((n) => !bridgeFirmwareFiles.contains(n));
    if (unknown.isNotEmpty) {
      bridgeUpdateResult = 'Not a radio firmware file: ${unknown.join(', ')}';
      session.notifyListeners();
      return false;
    }
    final names = [
      for (final name in bridgeFirmwareFiles)
        if (files.containsKey(name)) name,
    ];
    final bundle = files['usb_master_bundle.mpy'];
    final expected = bundle == null ? null : embeddedBridgeVersion(bundle);
    final total = files.values.fold<int>(0, (sum, f) => sum + f.length);
    bridgeUpdateResult = null;
    final stored = await _operation('Radio firmware update', (epoch) async {
      _requireDisarmed();
      if (names.isEmpty) throw ArgumentError('Choose radio firmware files');
      for (final name in names) {
        if (files[name]!.isEmpty || files[name]!.length > 256 * 1024) {
          throw ArgumentError('$name must be 1 byte to 256 KiB');
        }
      }
      _cancelUpdate = false;
      var done = 0;
      try {
        for (final name in names) {
          final bytes = files[name]!;
          await _sendBridgeFile(name, bytes, epoch, (sent) {
            updateProgress = (done + sent) / total;
            status = 'Radio update: $name ${sent * 100 ~/ bytes.length}%';
            session.notifyListeners();
          });
          done += bytes.length;
        }
      } finally {
        updateProgress = null;
      }
    });
    if (!stored) {
      bridgeUpdateResult = status;
      session.notifyListeners();
      return false;
    }
    bridgeUpdateResult = 'Files stored. Restarting the radio…';
    session.notifyListeners();
    try {
      // The bridge replies, then resets about 150 ms later.
      await session._request(0x31);
    } catch (_) {
      /* The reset can beat the reply; reconnecting below tells. */
    }
    await session.disconnect();
    final connected = await session.reconnect(delay: reconnectDelay);
    final running = session.values['Firmware'];
    if (!connected) {
      bridgeUpdateResult =
          'Files stored, but the radio did not reconnect. Reconnect it from '
          'Radio; it keeps the update once the app connects.';
    } else if (expected != null && running != expected) {
      bridgeUpdateResult = 'Radio restarted with $running, not $expected.';
    } else {
      bridgeUpdateResult = 'Radio updated • running $running';
    }
    session.notifyListeners();
    return connected && (expected == null || running == expected);
  }

  Future<bool> rebootModule(int uid) =>
      _operation('Reboot module', (epoch) async {
        _requireDisarmed();
        await _ack(uid, 0x64, epoch);
        session.modules[uid]?.seenInScan = false;
        uploaded.clear();
        document = null;
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
