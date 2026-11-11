import 'radio_session.dart';

enum SystemArmLevel { unknown, safe, commandArmed, systemArmed, error }

/// The whole system's arm state for the bar at the top of the app: the
/// command module's ARM key together with the modules that confirmed ARM,
/// plus anything that is wrong.
class SystemArmStatus {
  const SystemArmStatus(this.level, this.label, [this.error]);
  final SystemArmLevel level;
  final String label;

  /// What is wrong, when [level] is [SystemArmLevel.error] (and for a few
  /// warnings that don't change the label).
  final String? error;

  static String _modules(int count) =>
      count == 1 ? '1 module' : '$count modules';

  static SystemArmStatus from(RadioSession session) {
    if (!session.ready) {
      return const SystemArmStatus(SystemArmLevel.unknown, 'ARM STATE UNKNOWN');
    }
    final master = session.master;
    final status = session.interfaceStatus;
    final armed = master.armed.length;

    // Errors first: they are what the operator has to act on.
    if (session.armKeyLockout) {
      return const SystemArmStatus(
        SystemArmLevel.error,
        'ARM KEY ERROR',
        'The ARM key was on when the radio started or connected. Turn it to SAFE.',
      );
    }
    if (master.stopping) {
      return const SystemArmStatus(
        SystemArmLevel.error,
        'STOPPING',
        'Sending STOP and DISARM to every module.',
      );
    }
    if (master.interrupted) {
      return const SystemArmStatus(
        SystemArmLevel.error,
        'STOPPED',
        'STOP was sent. Press DISARM ALL to confirm every module is safe.',
      );
    }
    if (status != null) {
      final fault = _fault(status.faults);
      if (fault != null) {
        return SystemArmStatus(
          SystemArmLevel.error,
          'COMMAND MODULE FAULT',
          fault,
        );
      }
    }
    final hasKey = status?.switchPin != null;
    final keyArmed = hasKey && status!.switchPosition == 2;
    if (hasKey && status!.switchPosition == 0) {
      return const SystemArmStatus(
        SystemArmLevel.error,
        'ARM KEY UNKNOWN',
        'The ARM key reading is changing or unreadable.',
      );
    }
    if (hasKey && !keyArmed && armed > 0) {
      return SystemArmStatus(
        SystemArmLevel.error,
        'MODULES ARMED, KEY SAFE',
        '${_modules(armed)} confirmed ARM but the key is on SAFE. Press DISARM ALL.',
      );
    }
    final lastError = master.lastError;

    SystemArmStatus withError(SystemArmLevel level, String label) =>
        SystemArmStatus(level, label, lastError);

    if (!master.supported) {
      // Monitor firmware: no arming through this bridge.
      if (status == null) {
        return const SystemArmStatus(SystemArmLevel.unknown, 'ARM STATE UNKNOWN');
      }
      return keyArmed
          ? withError(SystemArmLevel.commandArmed, 'COMMAND MODULE ARMED')
          : withError(SystemArmLevel.safe, 'SYSTEM SAFE');
    }
    if (armed > 0 && (keyArmed || !hasKey)) {
      return withError(
        SystemArmLevel.systemArmed,
        'SYSTEM ARMED • ${_modules(armed)}',
      );
    }
    if (keyArmed) {
      return withError(SystemArmLevel.commandArmed, 'COMMAND MODULE ARMED');
    }
    if (status == null) {
      return withError(SystemArmLevel.unknown, 'ARM STATE UNKNOWN');
    }
    return withError(SystemArmLevel.safe, 'SYSTEM SAFE');
  }

  /// Command module faults that matter for arming. The LED fault and the
  /// start-up key latch are reported elsewhere.
  static String? _fault(int faults) {
    final problems = [
      if (faults & 1 != 0) 'ARM key switch fault',
      if (faults & 8 != 0) 'radio unavailable',
      if (faults & 16 != 0) 'RF heartbeat to the modules failing',
      if (faults & 4 != 0) 'app heartbeat expired',
    ];
    if (problems.isEmpty) return null;
    final text = problems.join(', ');
    return '${text[0].toUpperCase()}${text.substring(1)}.';
  }
}
