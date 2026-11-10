import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../protocol/bridge_codec.dart';
import '../protocol/nfcp_codec.dart';
import '../protocol/interface_status.dart';
import '../transport/radio_transport.dart';
import 'field_module.dart';
import '../protocol/master_codec.dart';
import '../scripts/script_document.dart';

part 'master_controller.dart';

/// A field module's NACK (0x51) for [command] with its reason [code].
class ModuleRejection extends StateError {
  ModuleRejection(this.command, this.code, super.message);
  final int command, code;
}

class RadioSession extends ChangeNotifier {
  RadioSession(
    this.transport, {
    this.discoveryRounds = 4,
    this.discoveryInterval = const Duration(milliseconds: 900),
    this.moduleTimeout = const Duration(seconds: 4),
  }) {
    _reader = transport.incoming.listen(
      _receive,
      onError: (Object error) {
        status = '$error';
        ready = false;
        _epoch++;
        _cancelPending();
        for (final module in modules.values) {
          module.seenInScan = false;
        }
        notifyListeners();
      },
    );
    unawaited(_loadPreferences());
  }
  final RadioTransport transport;
  late final MasterController master = MasterController(this);
  final int discoveryRounds;
  final Duration discoveryInterval;
  final Duration moduleTimeout;
  final modules = <int, FieldModule>{};
  int? _discoverNonce;
  int _nonce = 0;
  Completer<NfcpPacket>? _modulePending;
  int? _moduleUid;
  int? _moduleCommand;
  int? _moduleResponse;
  Completer<int>? _groupReadback;
  int? _groupReadbackUid;
  bool get canSetModuleGroup =>
      canQueryModules &&
      interfaceStatus?.supportsGroupSettings == true &&
      master.armed.isEmpty &&
      !master.running &&
      !master.stepping;
  bool get canQueryModules =>
      ready &&
      master.armed.isEmpty &&
      !master.running &&
      !master.stepping &&
      (values['Firmware']?.startsWith('usb-monitor-0.2') == true ||
          values['Firmware']?.startsWith('usb-monitor-0.3') == true ||
          isMasterFirmware(values['Firmware']));
  InterfaceStatus? interfaceStatus;
  MasterHeartbeatStatus? masterHeartbeatStatus;

  /// Safety lockout: the bridge's ARM key was on when the app connected (or
  /// when the bridge powered up) and has not been turned to SAFE since.
  /// Arming is refused while it is set. The bridge reports it itself
  /// (usb-master-0.4.3+); the app also latches it from the first status
  /// after connecting, so older bridge firmware is covered too.
  bool get armKeyLockout =>
      _armKeyLatch || interfaceStatus?.armKeyLatched == true;
  bool _armKeyLatch = false;

  void _updateArmKeyLatch(InterfaceStatus status, bool firstStatus) {
    if (status.switchPin == null) {
      _armKeyLatch = false;
    } else if (status.switchPosition == 1) {
      _armKeyLatch = false;
    } else if (firstStatus && status.switchPosition == 2) {
      _armKeyLatch = true;
    }
  }
  Timer? _heartbeatTimer;
  bool _heartbeatInFlight = false;
  int _hostSession = 0;
  late final StreamSubscription<List<int>> _reader;
  UsbLineDecoder _decoder = UsbLineDecoder();
  final _pending = <int, (int, Completer<BridgeFrame>)>{};
  int _sequence = 0;
  bool busy = false;
  String? continuityProgress;

  /// After discovery, each heartbeat checks the continuity of one random
  /// discovered module while nothing is armed, running or busy. Saved on
  /// this device (Settings tab).
  static const autoContinuityKey = 'auto_continuity_v1';
  bool _autoContinuity = true;
  bool get autoContinuity => _autoContinuity;
  set autoContinuity(bool enabled) {
    if (_autoContinuity == enabled) return;
    _autoContinuity = enabled;
    notifyListeners();
    unawaited(_savePreferences());
  }

  Future<void> _loadPreferences() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getBool(autoContinuityKey);
      if (saved != null && !_disposed && saved != _autoContinuity) {
        _autoContinuity = saved;
        notifyListeners();
      }
    } catch (_) {
      // No storage (tests): keep the default.
    }
  }

  Future<void> _savePreferences() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool(autoContinuityKey, _autoContinuity);
    } catch (_) {}
  }

  Future<void>? _autoCheck;
  final _random = math.Random();
  bool get autoContinuityRunning => _autoCheck != null;
  bool ready = false;
  String status = 'Connect the radio using a USB data cable.';
  Map<String, String> values = {};
  DateTime? lastUpdate;
  int _epoch = 0;
  bool _disposed = false;

  @override
  void notifyListeners() {
    if (!_disposed) super.notifyListeners();
  }

  void _cancelPending() {
    master.invalidate();
    _heartbeatTimer?.cancel();
    _heartbeatTimer = null;
    interfaceStatus = null;
    _armKeyLatch = false;
    masterHeartbeatStatus = null;
    _discoverNonce = null;
    final groupReadback = _groupReadback;
    _groupReadback = null;
    _groupReadbackUid = null;
    if (groupReadback != null && !groupReadback.isCompleted) {
      groupReadback.completeError(StateError('Session ended'));
    }
    final modulePending = _modulePending;
    _modulePending = null;
    if (modulePending != null && !modulePending.isCompleted) {
      modulePending.completeError(StateError('Session ended'));
    }
    for (final pending in _pending.values.toList()) {
      if (!pending.$2.isCompleted) {
        pending.$2.completeError(StateError('Session ended'));
      }
    }
    _pending.clear();
  }

  void _receive(List<int> bytes) {
    for (final frame in _decoder.add(bytes)) {
      if (frame.command == 0x11 &&
          frame.flags == 0 &&
          frame.payload.length >= 3) {
        final packet = decodeNfcp(frame.payload.sublist(3));
        if (packet != null && packet.kind == 0xfa) {
          var rssi = readUnsigned(frame.payload, 0, 2);
          if (rssi >= 32768) rssi -= 65536;
          // Mesh bridges put the frame's hop count in the third byte
          // (always 0 before).
          _receiveModule(packet, rssi, frame.payload[2]);
        }
        continue;
      }
      final pending = _pending[frame.sequence];
      if (pending == null ||
          frame.flags & 2 == 0 ||
          frame.command != pending.$1) {
        continue;
      }
      if (frame.flags & 4 != 0) {
        pending.$2.completeError(
          StateError('Bridge rejected command ${frame.command}'),
        );
      } else {
        pending.$2.complete(frame);
      }
      _pending.remove(frame.sequence);
    }
  }

  Future<BridgeFrame> _request(
    int command, [
    List<int> payload = const [],
    Duration timeout = const Duration(seconds: 4),
  ]) async {
    _sequence = (_sequence + 1) & 255;
    final sequence = _sequence;
    final completion = Completer<BridgeFrame>();
    _pending[sequence] = (command == 1 ? 2 : command, completion);
    try {
      final results =
          await Future.wait<Object?>([
            transport.write(usbLine(BridgeFrame(sequence, command, payload))),
            completion.future,
          ], eagerError: true).timeout(
            timeout,
            onTimeout: () {
              throw TimeoutException(
                'Radio did not reply to command 0x${command.toRadixString(16)}. Check USB monitor firmware.',
              );
            },
          );
      return results[1] as BridgeFrame;
    } finally {
      _pending.remove(sequence);
    }
  }

  void _receiveModule(NfcpPacket packet, int rssi, [int hops = 0]) {
    if (_discoverNonce != null && packet.command == 0x41) {
      final data = packet.data;
      if (data.length != 10 && data.length != 12) return;
      if (data.length == 12 && readUnsigned(data, 10, 2) != _discoverNonce) {
        return;
      }
      if (readUnsigned(data, 2, 4) != packet.source ||
          data[6] < 1 ||
          data[6] > 64) {
        return;
      }
      final previous = modules[packet.source];
      final module = previous != null && previous.cueCount == data[6]
          ? previous
          : FieldModule(
              packet.source,
              readUnsigned(data, 0, 2),
              data[6],
              data[7],
              protocolVersion: data[8],
            );
      module.lastSeen = DateTime.now();
      module.group = readUnsigned(data, 0, 2);
      module.seenInScan = true;
      module.rssi = rssi;
      module.hops = hops;
      modules[packet.source] = module;
      if (packet.source == _groupReadbackUid &&
          _groupReadback != null &&
          !_groupReadback!.isCompleted) {
        _groupReadback!.complete(module.group);
      }
      notifyListeners();
    }
    final pending = _modulePending;
    if (pending == null || pending.isCompleted || packet.source != _moduleUid) {
      return;
    }
    final rejected =
        packet.command == 0x51 &&
        packet.data.length == 2 &&
        packet.data[0] == _moduleCommand;
    if (packet.command != _moduleResponse && !rejected) return;
    if (packet.command == 0x50 &&
        (packet.data.length != 2 || packet.data[0] != _moduleCommand)) {
      return;
    }
    final module = modules[packet.source];
    if (module != null) {
      module.lastSeen = DateTime.now();
      module.rssi = rssi;
      module.hops = hops;
    }
    if (rejected) {
      pending.completeError(
        ModuleRejection(
          _moduleCommand!,
          packet.data[1],
          _moduleCommand == 0x31 && packet.data[1] == 1
              ? 'Module is armed. Disarm it before changing its group.'
              : 'Module rejected request (code ${packet.data[1]})',
        ),
      );
    } else {
      pending.complete(packet);
    }
  }

  Future<NfcpPacket> _queryModule(
    int uid,
    int command,
    int response, {
    List<int>? frame,
    Duration? timeout,
  }) async {
    final completion = Completer<NfcpPacket>();
    _modulePending = completion;
    _moduleUid = uid;
    _moduleCommand = command;
    _moduleResponse = response;
    final request = frame ?? moduleQuery(uid, command);
    final wait = timeout ?? moduleTimeout;
    // A mesh bridge delivers the request itself (route, retries with the
    // same message id, then flood); the reply still arrives as a normal
    // module frame and completes `completion`.
    final reliable = supportsReliableDelivery(values['Firmware']);
    final send = reliable
        ? _request(
            0x1a,
            reliablePayload(uid, wait, request),
            wait + const Duration(seconds: 1),
          ).then((answer) {
            final delivery = Delivery.parse(answer.payload);
            final module = modules[uid];
            if (delivery != null && module != null) {
              module.attempts = delivery.attempts;
              module.routeAt = DateTime.now();
              module.lastDeliveryFailed = !delivery.delivered;
              if (delivery.delivered) {
                module.route = delivery.route;
                module.relay = delivery.relay;
                module.routeCounts.update(
                  delivery.route,
                  (count) => count + 1,
                  ifAbsent: () => 1,
                );
              } else {
                module.failedDeliveries++;
              }
            }
            if (delivery != null && !delivery.delivered) {
              throw TimeoutException(
                'No reply from module ${modules[uid]?.id ?? uid} '
                '(${delivery.attempts} attempts)',
              );
            }
            return answer;
          })
        : _request(0x10, request);
    try {
      final results =
          await Future.wait<Object?>([
            send,
            completion.future,
          ], eagerError: true).timeout(
            reliable ? wait + const Duration(seconds: 1) : wait,
            onTimeout: () {
              throw TimeoutException(
                'No reply from module ${modules[uid]?.id ?? uid}',
              );
            },
          );
      return results[1] as NfcpPacket;
    } finally {
      if (identical(_modulePending, completion)) _modulePending = null;
    }
  }

  Future<void> discoverModules() async {
    if (busy || !canQueryModules) return;
    busy = true;
    await waitForAutoContinuity();
    final epoch = _epoch;
    for (final module in modules.values) {
      module.seenInScan = false;
    }
    _nonce = (_nonce + DateTime.now().microsecond + 1) & 65535;
    _discoverNonce = _nonce;
    status = 'Discovering field modules…';
    notifyListeners();
    try {
      for (var round = 0; round < discoveryRounds; round++) {
        if (epoch != _epoch || !ready) return;
        await _request(0x10, discoverQuery(_nonce));
        await Future<void>.delayed(discoveryInterval);
      }
      _discoverNonce = null;
      for (final module
          in modules.values.where((module) => module.seenInScan).toList()) {
        if (epoch != _epoch || !ready) return;
        try {
          module.info = moduleInfo(
            (await _queryModule(module.uid, 0x52, 0x53)).data,
          );
          module.error = null;
        } catch (error) {
          module.error = '$error';
        }
      }
      if (epoch != _epoch) return;
      final count = modules.values.where((module) => module.seenInScan).length;
      status = count == 0
          ? 'No field modules replied. Check their power, network and channel.'
          : '$count field module${count == 1 ? '' : 's'} discovered';
    } catch (error) {
      if (epoch == _epoch) status = '$error';
    } finally {
      _discoverNonce = null;
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> setModuleGroup(int uid, int group) async {
    final module = modules[uid];
    if (busy || !canSetModuleGroup || module == null || !module.seenInScan) {
      return false;
    }
    if (group < 0 || group > 65535) {
      throw ArgumentError('Group must be 0–65535.');
    }
    if (group == module.group) return true;
    if (modules.values.any(
      (other) => other.uid != uid && other.group == group,
    )) {
      throw ArgumentError(
        'Group $group is already assigned to another discovered module.',
      );
    }
    busy = true;
    await waitForAutoContinuity();
    final epoch = _epoch;
    module.error = null;
    status = 'Saving group $group on ${module.id}…';
    notifyListeners();
    try {
      final ack = await _queryModule(
        uid,
        0x31,
        0x50,
        frame: moduleGroupSetting(uid, group),
      );
      if (epoch != _epoch || !ready) return false;
      if (ack.data.length != 2 || ack.data[0] != 0x31 || ack.data[1] != 0) {
        throw StateError('Module did not acknowledge the group change.');
      }
      final readback = Completer<int>();
      _groupReadback = readback;
      _groupReadbackUid = uid;
      _nonce = (_nonce + DateTime.now().microsecond + 1) & 65535;
      _discoverNonce = _nonce;
      status = 'Confirming group $group on ${module.id}…';
      notifyListeners();
      int? confirmed;
      for (var round = 0; round < discoveryRounds; round++) {
        if (epoch != _epoch || !ready) return false;
        try {
          final replies =
              await Future.wait<Object>([
                _request(0x10, discoverQuery(_nonce)),
                readback.future,
              ], eagerError: true).timeout(
                discoveryInterval == Duration.zero
                    ? moduleTimeout
                    : discoveryInterval,
              );
          confirmed = replies[1] as int;
          break;
        } on TimeoutException {
          if (round == discoveryRounds - 1) rethrow;
        }
      }
      if (epoch != _epoch || !ready) return false;
      if (confirmed != group) {
        throw StateError('Group readback was $confirmed; expected $group.');
      }
      status = '${module.id} group $group saved and confirmed';
      return true;
    } catch (error) {
      if (epoch == _epoch) {
        module.seenInScan = false;
        module.error =
            'Group change unconfirmed: $error. Run discovery before using this module.';
        status = module.error!;
      }
      return false;
    } finally {
      _groupReadback = null;
      _groupReadbackUid = null;
      _discoverNonce = null;
      busy = false;
      notifyListeners();
    }
  }

  Future<void> readModule(int uid, {bool resistance = false}) async {
    final module = modules[uid];
    if (busy || !canQueryModules || module == null) return;
    busy = true;
    try {
      await waitForAutoContinuity();
      await _readModule(uid, _epoch, resistance: resistance);
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> checkAllContinuity() async {
    if (busy || !canQueryModules || modules.isEmpty) return;
    busy = true;
    await waitForAutoContinuity();
    final epoch = _epoch;
    final uids = modules.keys.toList()..sort();
    var updated = 0;
    var failed = 0;
    try {
      for (var index = 0; index < uids.length; index++) {
        if (epoch != _epoch || !canQueryModules) return;
        continuityProgress = 'Checking module ${index + 1} of ${uids.length}…';
        notifyListeners();
        final success = await _readModule(uids[index], epoch, resistance: true);
        if (epoch != _epoch || !canQueryModules) return;
        if (success) {
          updated++;
        } else {
          failed++;
        }
      }
      status = 'Continuity check complete: $updated updated, $failed failed.';
    } finally {
      continuityProgress = null;
      busy = false;
      notifyListeners();
    }
  }

  Future<bool> _readModule(
    int uid,
    int epoch, {
    required bool resistance,
  }) async {
    final module = modules[uid];
    if (module == null || epoch != _epoch || !canQueryModules) return false;
    module.error = null;
    if (resistance) {
      module.resistance = null;
      module.measuredAt = null;
    }
    status = 'Reading ${module.id}…';
    notifyListeners();
    try {
      NfcpPacket reply;
      var continuityOnly = false;
      try {
        reply = await _queryModule(
          uid,
          resistance ? 0x24 : 0x52,
          resistance ? 0x25 : 0x53,
        );
      } on TimeoutException {
        if (!resistance || epoch != _epoch || !ready) rethrow;
        status = 'Trying legacy continuity for ${module.id}…';
        notifyListeners();
        reply = await _queryModule(uid, 0x22, 0x23);
        continuityOnly = true;
      }
      if (epoch != _epoch) return false;
      if (resistance) {
        module.resistance = continuityOnly
            ? ResistanceSnapshot.parseContinuity(reply.data, module.cueCount)
            : ResistanceSnapshot.parse(reply.data, module.cueCount);
        module.measuredAt = DateTime.now();
        module.noReply = false;
      } else {
        module.info = moduleInfo(reply.data);
      }
      status =
          '${module.id} ${resistance ? 'continuity' : 'information'} updated';
      return true;
    } catch (error) {
      if (epoch == _epoch) {
        module.error = '$error';
        status = '$error';
        if (resistance && error is TimeoutException) module.noReply = true;
      }
      return false;
    } finally {
      notifyListeners();
    }
  }

  String? _deviceId;

  /// Opens the last radio again, e.g. after a firmware update restarted it.
  /// The radio's USB port disappears for a few seconds while it restarts.
  Future<bool> reconnect({
    int attempts = 10,
    Duration delay = const Duration(seconds: 2),
  }) async {
    final id = _deviceId;
    if (id == null) return false;
    for (var attempt = 0; attempt < attempts && !_disposed; attempt++) {
      await Future<void>.delayed(delay);
      if (_disposed) return false;
      await connect(id);
      if (ready) return true;
    }
    return false;
  }

  Future<void> connect(String id) async {
    if (busy || master.stopping) return;
    if (ready) await disconnect();
    _deviceId = id;
    final epoch = ++_epoch;
    busy = true;
    ready = false;
    values = {};
    modules.clear();
    lastUpdate = null;
    status = 'Opening USB connection…';
    notifyListeners();
    try {
      _decoder = UsbLineDecoder(
        onConsole: (line) => debugPrint('Radio console: $line'),
      );
      await transport.connect(id);
      if (epoch != _epoch) return;
      await _request(1);
      values = bridgeInfo((await _request(3)).payload);
      values = {...values, ...bridgeStats((await _request(0x15)).payload)};
      if (epoch != _epoch) return;
      lastUpdate = DateTime.now();
      ready = true;
      status = 'Radio replied over USB';
      master.interrupted = false;
      if (values['Firmware']?.startsWith('usb-monitor-0.3') == true ||
          master.supported) {
        _hostSession = DateTime.now().microsecondsSinceEpoch & 0xffffffff;
        heartbeatMisses = 0;
        await _heartbeat();
        if (epoch != _epoch || !ready) return;
        _heartbeatTimer = Timer.periodic(
          const Duration(seconds: 1),
          (_) => unawaited(_heartbeat()),
        );
      }
    } catch (error) {
      if (epoch != _epoch) return;
      status = '$error';
      await transport.disconnect();
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  /// Heartbeat replies may be lost on some Android USB hosts. One miss is
  /// tolerated; two in a row (about 2 s, inside the bridge's 3 s host
  /// timeout) end the session. Bridge rejections still end it immediately.
  static const heartbeatReplyTimeout = Duration(milliseconds: 900);
  static const maxHeartbeatMisses = 2;
  int heartbeatMisses = 0;

  Future<void> _heartbeat() async {
    if (_heartbeatInFlight || !ready || _disposed) return;
    final epoch = _epoch;
    _heartbeatInFlight = true;
    try {
      await _request(0x18, bigEndian(_hostSession, 4), heartbeatReplyTimeout);
      final reply = await _request(0x17, const [], heartbeatReplyTimeout);
      if (epoch != _epoch) return;
      heartbeatMisses = 0;
      final firstStatus = interfaceStatus == null;
      interfaceStatus = InterfaceStatus.parse(reply.payload);
      _updateArmKeyLatch(interfaceStatus!, firstStatus);
      if (master.supported &&
          master.armed.isNotEmpty &&
          interfaceStatus!.switchPin != null &&
          interfaceStatus!.switchPosition != 2) {
        await master.emergencyStop();
      }
      if (interfaceStatus!.supportsMasterHeartbeat) {
        final keepalive = await _request(0x19, const [], heartbeatReplyTimeout);
        if (epoch != _epoch) return;
        masterHeartbeatStatus = MasterHeartbeatStatus.parse(keepalive.payload);
      }
      notifyListeners();
      _startAutoContinuity();
    } on TimeoutException catch (error) {
      if (epoch != _epoch) return;
      heartbeatMisses++;
      if (heartbeatMisses < maxHeartbeatMisses) {
        debugPrint('Heartbeat reply missed ($heartbeatMisses): $error');
        return;
      }
      await _heartbeatLost(epoch, error);
    } catch (error) {
      await _heartbeatLost(epoch, error);
    } finally {
      _heartbeatInFlight = false;
    }
  }

  /// Completes once no automatic continuity check is in flight.
  Future<void> waitForAutoContinuity() async {
    while (_autoCheck != null) {
      await _autoCheck;
    }
  }

  void _startAutoContinuity() {
    if (!autoContinuity ||
        _autoCheck != null ||
        busy ||
        !canQueryModules ||
        master.stopping ||
        _discoverNonce != null ||
        // The operator is about to arm: keep the radio free for it.
        interfaceStatus?.switchPosition == 2) {
      return;
    }
    final candidates = modules.values
        .where((module) => module.seenInScan)
        .toList();
    if (candidates.isEmpty) return;
    final module = candidates[_random.nextInt(candidates.length)];
    final check = _autoContinuityCheck(module, _epoch);
    _autoCheck = check;
    check.whenComplete(() {
      if (identical(_autoCheck, check)) _autoCheck = null;
    });
  }

  Future<void> _autoContinuityCheck(FieldModule module, int epoch) async {
    try {
      final reply = await _queryModule(module.uid, 0x24, 0x25);
      if (epoch != _epoch) return;
      module.resistance = ResistanceSnapshot.parse(reply.data, module.cueCount);
      module.measuredAt = DateTime.now();
      module.error = null;
      module.noReply = false;
      module.replyFlash++;
    } catch (error) {
      if (epoch == _epoch && !master.stopping) {
        module.error = 'Automatic continuity check: $error';
        if (error is TimeoutException) module.noReply = true;
      }
    } finally {
      notifyListeners();
    }
  }

  Future<void> _heartbeatLost(int epoch, Object error) async {
    if (epoch != _epoch) return;
    ready = false;
    _epoch++;
    _cancelPending();
    status = 'Command-interface heartbeat lost: $error';
    await transport.disconnect();
    notifyListeners();
  }

  Future<void> refresh() async {
    if (busy || !ready) return;
    final epoch = _epoch;
    busy = true;
    notifyListeners();
    try {
      values = {...values, ...bridgeStats((await _request(0x15)).payload)};
      lastUpdate = DateTime.now();
      status = 'Radio statistics updated';
    } catch (error) {
      if (epoch != _epoch) return;
      ready = false;
      _epoch++;
      _cancelPending();
      status = '$error';
      await transport.disconnect();
    } finally {
      busy = false;
      notifyListeners();
    }
  }

  Future<void> disconnect() async {
    _epoch++;
    ready = false;
    _cancelPending();
    await transport.disconnect();
    values = {};
    modules.clear();
    lastUpdate = null;
    status = 'Disconnected';
    notifyListeners();
  }

  @override
  void dispose() {
    _disposed = true;
    _epoch++;
    _cancelPending();
    unawaited(_reader.cancel());
    unawaited(transport.dispose());
    super.dispose();
  }
}
