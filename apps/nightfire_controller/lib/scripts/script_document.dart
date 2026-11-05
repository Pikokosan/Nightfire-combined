import '../controller/field_module.dart';

class ScriptFinding {
  const ScriptFinding(
    this.message, {
    this.line,
    this.error = true,
    this.moduleUid,
    this.group,
    this.cue,
  });
  final String message;
  final int? line;
  final bool error;
  final int? moduleUid, group, cue;
  ModuleFocus get focus => ModuleFocus(uid: moduleUid, group: group, cue: cue);
  String get text => '${line == null ? '' : 'Line $line: '}$message';
}

class ScriptEvent {
  const ScriptEvent(
    this.line,
    this.time,
    this.timeMs,
    this.channel,
    this.cue,
    this.description,
  );
  final int line, timeMs, channel, cue;
  final String time, description;
}

class ScriptDocument {
  ScriptDocument(
    this.filename,
    this.name,
    this.events,
    this.findings,
    this.auxiliaryCount,
  );
  static const maxBytes = 1024 * 1024;
  final String filename, name;
  final List<ScriptEvent> events;
  final List<ScriptFinding> findings;
  final int auxiliaryCount;
  late final Set<(int, int)> _assignedCues = {
    for (final event in events) (event.channel, event.cue),
  };
  bool usesCue(int group, int cue) => _assignedCues.contains((group, cue));

  static ScriptDocument parse(String filename, String source) {
    if (source.length > maxBytes) {
      throw const FormatException('Script exceeds the 1 MiB limit.');
    }
    final findings = <ScriptFinding>[];
    final events = <ScriptEvent>[];
    var name = filename;
    var auxiliary = 0;
    var eventHeader = false;
    var ended = false;
    List<String>? triggerHeader;
    var previousTime = -1;
    final usedCues = <String>{};
    for (final (line, cells) in _csv(
      source.replaceFirst(RegExp(r'^\uFEFF'), ''),
    )) {
      if (cells.every((cell) => cell.isEmpty)) continue;
      final first = cells.first;
      if (ended) {
        if (!first.startsWith('#')) {
          findings.add(
            ScriptFinding('Content after END is not checked.', line: line),
          );
        }
        continue;
      }
      if (first.toUpperCase() == 'END') {
        ended = true;
        continue;
      }
      if (first == '#Trigger Channel') {
        triggerHeader = cells;
        continue;
      }
      if (first == '#Event Time') {
        if (eventHeader) {
          findings.add(ScriptFinding('Repeated event header.', line: line));
        }
        if (cells.length < 4 ||
            cells[1].replaceFirst('#', '').toLowerCase() != 'channel' ||
            cells[2].replaceFirst('#', '').toLowerCase() != 'cue') {
          throw FormatException(
            'Line $line: expected #Event Time, Channel, Cue, Description columns.',
          );
        }
        eventHeader = true;
        triggerHeader = null;
        continue;
      }
      if (first.startsWith('#')) continue;
      if (triggerHeader != null) {
        final index = triggerHeader.indexOf('#Script Name');
        if (index >= 0 && index < cells.length && cells[index].isNotEmpty) {
          name = cells[index];
        }
        triggerHeader = null;
        continue;
      }
      if (!eventHeader) {
        findings.add(
          ScriptFinding('Unexpected row before the event header.', line: line),
        );
        continue;
      }
      if (cells.length < 4) {
        findings.add(
          ScriptFinding('Event needs at least four columns.', line: line),
        );
        continue;
      }
      final time = parseTime(cells[0]);
      if (time == null) {
        findings.add(
          ScriptFinding('Invalid event time: ${cells[0]}', line: line),
        );
        continue;
      }
      if (time < previousTime) {
        findings.add(
          ScriptFinding('Event times are out of order.', line: line),
        );
      }
      previousTime = time;
      if (cells[1].isEmpty && cells[2].isEmpty) {
        auxiliary++;
        continue;
      }
      final channel = int.tryParse(cells[1]);
      final cue = int.tryParse(cells[2]);
      if (channel == null ||
          channel < 0 ||
          channel > 65535 ||
          cue == null ||
          cue < 1 ||
          cue > 64) {
        findings.add(
          ScriptFinding(
            'Channel must be 0–65535 and cue must be 1–64.',
            line: line,
          ),
        );
        continue;
      }
      if (!usedCues.add('$channel:$cue')) {
        findings.add(
          ScriptFinding(
            'Channel $channel cue $cue is used more than once.',
            line: line,
          ),
        );
      }
      events.add(ScriptEvent(line, cells[0], time, channel, cue, cells[3]));
      if (events.length > 10000) {
        throw const FormatException('Script exceeds 10,000 cue events.');
      }
    }
    if (!eventHeader) {
      findings.add(const ScriptFinding('Missing COBRA #Event Time header.'));
    }
    if (events.isEmpty) {
      findings.add(const ScriptFinding('No valid cue events found.'));
    }
    if (auxiliary > 0) {
      findings.add(
        ScriptFinding(
          '$auxiliary auxiliary/DMX rows are not supported or checked.',
          error: false,
        ),
      );
    }
    return ScriptDocument(filename, name, events, findings, auxiliary);
  }

  static int? parseTime(String text) {
    final value = text.trim().replaceFirst(RegExp(r'[sS]$'), '');
    if (!RegExp(r'^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$').hasMatch(value)) {
      return null;
    }
    final parts = value.split(':');
    final seconds = parts.last.split('.');
    final whole = int.tryParse(seconds[0]);
    if (whole == null || (parts.length > 1 && whole >= 60)) return null;
    final minutes = parts.length > 1
        ? int.tryParse(parts[parts.length - 2])
        : 0;
    final hours = parts.length == 3 ? int.tryParse(parts[0]) : 0;
    if (minutes == null ||
        hours == null ||
        (parts.length == 3 && minutes >= 60) ||
        whole > 4294967 ||
        minutes > 71582 ||
        hours > 1193) {
      return null;
    }
    final millis = seconds.length == 2
        ? int.parse(seconds[1].padRight(3, '0'))
        : 0;
    final result = hours * 3600000 + minutes * 60000 + whole * 1000 + millis;
    return result <= 0xffffffff ? result : null;
  }
}

// CSV records retain source line numbers, quoted commas/newlines and escaped quotes.
Iterable<(int, List<String>)> _csv(String source) sync* {
  final cells = <String>[];
  var cell = StringBuffer();
  var quoted = false;
  var closed = false;
  var line = 1;
  var startLine = 1;
  for (var index = 0; index < source.length; index++) {
    final char = source[index];
    if (quoted) {
      if (char == '"') {
        if (index + 1 < source.length && source[index + 1] == '"') {
          cell.write('"');
          index++;
        } else {
          quoted = false;
          closed = true;
        }
      } else {
        cell.write(char);
        if (char == '\n') line++;
      }
      continue;
    }
    if (char == '"') {
      if (closed || cell.toString().trim().isNotEmpty) {
        throw FormatException('Line $line: unexpected quote.');
      }
      cell.clear();
      quoted = true;
    } else if (char == ',' || char == '\n' || char == '\r') {
      cells.add(cell.toString().trim());
      cell = StringBuffer();
      closed = false;
      if (char != ',') {
        yield (startLine, List.of(cells));
        cells.clear();
        if (char == '\r' &&
            index + 1 < source.length &&
            source[index + 1] == '\n') {
          index++;
        }
        line++;
        startLine = line;
      }
    } else {
      if (closed && char.trim().isNotEmpty) {
        throw FormatException(
          'Line $line: unexpected text after quoted value.',
        );
      }
      cell.write(char);
    }
  }
  if (quoted) {
    throw FormatException('Line $startLine: unterminated quoted value.');
  }
  if (cell.isNotEmpty || cells.isNotEmpty) {
    yield (startLine, [...cells, cell.toString().trim()]);
  }
}

class CheckedScriptEvent {
  const CheckedScriptEvent(
    this.event,
    this.status,
    this.moduleId, {
    this.measuredAt,
  });
  final ScriptEvent event;
  final String status;
  final String? moduleId;
  final DateTime? measuredAt;
}

class ScriptReport {
  const ScriptReport(this.findings, this.events);
  final List<ScriptFinding> findings;
  final List<CheckedScriptEvent> events;
  int get errors => findings.where((finding) => finding.error).length;
  int get warnings => findings.length - errors;

  static ScriptReport check(
    ScriptDocument script,
    Iterable<FieldModule> modules, {
    required bool radioReady,
    DateTime? now,
  }) {
    final findings = [...script.findings];
    final rows = <CheckedScriptEvent>[];
    final groups = <int, List<FieldModule>>{};
    final clock = now ?? DateTime.now();
    for (final module in modules) {
      groups.putIfAbsent(module.group, () => []).add(module);
    }
    if (!radioReady) {
      findings.add(
        const ScriptFinding(
          'Radio disconnected: hardware check is unavailable.',
        ),
      );
    }
    final referenced = <String>{};
    for (final event in script.events) {
      referenced.add('${event.channel}:${event.cue}');
      final matches = groups[event.channel] ?? [];
      final current = matches.where((module) => module.seenInScan).toList();
      FieldModule? module;
      String status;
      if (!radioReady) {
        status = 'Radio offline';
      } else if (current.length > 1) {
        status =
            'Duplicate group ${event.channel}: ${current.map((m) => m.id).join(', ')}';
      } else if (current.isEmpty) {
        status = matches.isEmpty
            ? 'Missing group ${event.channel}'
            : 'Group ${event.channel} not seen in latest scan';
      } else {
        module = current.single;
        final age = module.measuredAt == null
            ? null
            : clock.difference(module.measuredAt!);
        if (event.cue > module.cueCount) {
          status = 'Cue out of range (module has ${module.cueCount})';
        } else if (module.error != null) {
          status = 'Module query failed: ${module.error}';
        } else if (module.resistance == null || age == null) {
          status = 'Continuity unmeasured';
        } else if (age.isNegative) {
          status = 'Measurement timestamp is in the future';
        } else if (module.resistance!.status != 0) {
          status = 'Measurement warning: ${module.resistance!.warning}';
        } else {
          final reading = module.resistance!.cues[event.cue - 1];
          status = reading.label == 'Connected'
              ? 'Connected'
              : '${reading.label}: ${reading.detail}';
        }
      }
      rows.add(
        CheckedScriptEvent(
          event,
          status,
          module?.id,
          measuredAt: module?.measuredAt,
        ),
      );
      if (status != 'Connected') {
        findings.add(
          ScriptFinding(
            'Channel ${event.channel}, cue ${event.cue}: $status',
            line: event.line,
            moduleUid: module?.uid,
            group: event.channel,
            cue: event.cue,
          ),
        );
      }
    }
    if (radioReady) {
      for (final module in modules.where(
        (m) => m.seenInScan && m.resistance != null,
      )) {
        for (var index = 0; index < module.cueCount; index++) {
          if (module.resistance!.cues[index].label == 'Connected' &&
              !referenced.contains('${module.group}:${index + 1}')) {
            findings.add(
              ScriptFinding(
                '${module.id} group ${module.group}, cue ${index + 1}: connected in saved snapshot but not used by this script.',
                error: false,
                moduleUid: module.uid,
                group: module.group,
                cue: index + 1,
              ),
            );
          }
        }
      }
    }
    return ScriptReport(findings, rows);
  }
}
