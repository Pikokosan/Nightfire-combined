import 'bridge_codec.dart';

class InterfaceStatus {
  const InterfaceStatus(
    this.switchPosition,
    this.ledState,
    this.sessionState,
    this.faults,
    this.ageMs,
    this.timeoutMs,
    this.switchPin,
    this.ledPin,
    this.ledCount,
    this.rejected, {
    this.capabilities = 0,
  });
  final int switchPosition, ledState, sessionState, faults, ageMs, timeoutMs;
  final int? switchPin, ledPin;
  final int ledCount, rejected;
  final int capabilities;
  bool get supportsMasterHeartbeat => capabilities & 16 != 0;
  bool get supportsGroupSettings => capabilities & 32 != 0;

  /// Capability 64 (usb-master-0.4.4+): command 0x1B reads and sets the
  /// status LED brightness.
  bool get supportsLedBrightness => capabilities & 64 != 0;

  /// Command 0x1C opens a pairing window for field modules (usb-master-0.4.5+).
  bool get supportsPairing => capabilities & 4 != 0;

  /// The bridge relays the field cue hold time command (usb-master-0.4.5+).
  bool get supportsFirePulse => capabilities & 128 != 0;

  /// Fault bit 32 (usb-master-0.4.3+): the ARM key was on when the bridge
  /// powered up or the app connected, and has not been turned to SAFE since.
  /// The bridge refuses ARM and FIRE until it is.
  static const keyLatchedFault = 32;
  bool get armKeyLatched => faults & keyLatchedFault != 0;

  static InterfaceStatus parse(List<int> data) {
    if (data.length != 15 ||
        data[0] != 1 ||
        data[2] > 2 ||
        data[3] > 3 ||
        data[4] > 2 ||
        data[1] & 8 == 0) {
      throw const FormatException('Unsupported command-interface status');
    }
    return InterfaceStatus(
      data[2],
      data[3],
      data[4],
      data[5],
      readUnsigned(data, 6, 2),
      readUnsigned(data, 8, 2),
      data[10] == 255 ? null : data[10],
      data[11] == 255 ? null : data[11],
      data[12],
      readUnsigned(data, 13, 2),
      capabilities: data[1],
    );
  }

  String get switchText => switch (switchPosition) {
    2 when armKeyLatched => 'ARM position at start-up • turn to SAFE',
    1 => 'SAFE position',
    2 => 'ARM position • controls disabled',
    _ => switchPin == null ? 'Not configured' : 'Unknown / changing',
  };
  String get ledText => switch (ledState) {
    1 => '$ledCount pixels ready',
    2 => 'LED configuration/output fault',
    3 => 'Native LED driver required',
    _ => 'Not configured',
  };
  String get sessionText => switch (sessionState) {
    1 => 'Host heartbeat active',
    2 => 'Host heartbeat expired',
    _ => 'No host session',
  };
}

class MasterHeartbeatStatus {
  const MasterHeartbeatStatus(
    this.state,
    this.intervalMs,
    this.ageMs,
    this.sent,
    this.failures,
  );
  final int state, intervalMs, ageMs, sent, failures;

  static MasterHeartbeatStatus parse(List<int> data) {
    if (data.length != 14 || data[0] != 1 || data[1] > 2) {
      throw const FormatException('Unsupported RF heartbeat status');
    }
    return MasterHeartbeatStatus(
      data[1],
      readUnsigned(data, 2, 2),
      readUnsigned(data, 4, 2),
      readUnsigned(data, 6, 4),
      readUnsigned(data, 10, 4),
    );
  }

  String get text => switch (state) {
    1 => 'RF heartbeat active • every ${intervalMs / 1000} s • $sent sent',
    2 => 'RF heartbeat transmit failed • $failures failures',
    _ => 'RF heartbeat stopped',
  };
}
