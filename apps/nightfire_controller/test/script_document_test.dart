import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/scripts/script_document.dart';

const header = '#Event Time,#Channel,#Cue,#Description\n';
final now = DateTime(2026, 11, 3, 10);
FieldModule module(int uid, int group, {bool seen = true}) =>
    FieldModule(uid, group, 3, 1)
      ..seenInScan = seen
      ..measuredAt = now
      ..resistance = const ResistanceSnapshot(0, true, [
        CueReading('Connected', '2 Ω'),
        CueReading('Open', 'Open circuit'),
        CueReading('Short', '0 Ω'),
      ]);

void main() {
  test('COBRA metadata, BOM, quoted commas, escaped quotes and newlines', () {
    final doc = ScriptDocument.parse(
      'show.csv',
      '\uFEFF#Trigger Channel,#Trigger Button,#Script Name\r\n1,1,"Show, A"\r\n${header}00:01.250,1,1,"A ""quoted""\nlabel"\nEND\n',
    );
    expect(doc.name, 'Show, A');
    expect(doc.events.single.timeMs, 1250);
    expect(doc.events.single.description, 'A "quoted"\nlabel');
    expect(doc.events.single.line, 4);
    expect(doc.findings, isEmpty);
  });
  test('Time forms and 32-bit limits reject overflow or invalid fractions', () {
    expect(ScriptDocument.parseTime('1:02:03.045'), 3723045);
    expect(ScriptDocument.parseTime('1.5s'), 1500);
    expect(ScriptDocument.parseTime('4294967.295'), 0xffffffff);
    for (final invalid in [
      '-1',
      '1:60',
      '1:60:00',
      '1.0001',
      '4294967.296',
      '9223372036854775807',
      'NaN',
    ]) {
      expect(ScriptDocument.parseTime(invalid), isNull, reason: invalid);
    }
  });
  test('Malformed CSV and unexpected header cannot silently pass', () {
    expect(
      () => ScriptDocument.parse('bad.csv', '${header}0,1,1,"unclosed'),
      throwsFormatException,
    );
    expect(
      () => ScriptDocument.parse('bad.csv', '${header}0,1,1,"x"garbage'),
      throwsFormatException,
    );
    expect(
      () => ScriptDocument.parse(
        'bad.csv',
        '#Event Time,Cue,Channel,Description\n',
      ),
      throwsFormatException,
    );
    expect(
      ScriptDocument.parse(
        'bad.csv',
        '0,1,1,x\n',
      ).findings.any((f) => f.message.contains('Missing COBRA')),
      isTrue,
    );
  });
  test(
    'Invalid rows, repeated cues, ordering and content after END are reported',
    () {
      final doc = ScriptDocument.parse(
        'show.csv',
        '${header}2,1,1,a\n1,1,1,b\nbad,1,2,c\n3,1,,d\nEND\n4,1,2,ignored\n',
      );
      expect(doc.events.length, 2);
      expect(
        doc.findings.map((f) => f.message).join(),
        contains('out of order'),
      );
      expect(
        doc.findings.map((f) => f.message).join(),
        contains('more than once'),
      );
      expect(
        doc.findings.map((f) => f.message).join(),
        contains('Invalid event time'),
      );
      expect(
        doc.findings.map((f) => f.message).join(),
        contains('Content after END'),
      );
    },
  );
  test('Auxiliary rows are explicit unsupported warnings', () {
    final doc = ScriptDocument.parse('show.csv', '${header}0,1,1,a\n1,,,dmx\n');
    expect(doc.auxiliaryCount, 1);
    expect(doc.findings.single.error, isFalse);
  });
  test(
    'Checks group numbers rather than UID and reports cue states/ranges',
    () {
      final doc = ScriptDocument.parse(
        'show.csv',
        '${header}0,7,1,a\n1,7,2,b\n2,7,3,c\n3,7,4,d\n4,9,1,e\n',
      );
      final report = ScriptReport.check(
        doc,
        [module(0xabcdef, 7)],
        radioReady: true,
        now: now,
      );
      expect(report.events.map((row) => row.status), [
        'Connected',
        'Open: Open circuit',
        'Short: 0 Ω',
        'Cue out of range (module has 3)',
        'Missing group 9',
      ]);
      expect(report.events.first.moduleId, '0x00ABCDEF');
      expect(report.errors, 4);
    },
  );
  test('Duplicate or absent current groups and offline radio cannot pass', () {
    final doc = ScriptDocument.parse('show.csv', '${header}0,7,1,a\n');
    expect(
      ScriptReport.check(
        doc,
        [module(1, 7), module(2, 7)],
        radioReady: true,
        now: now,
      ).events.single.status,
      contains('Duplicate group'),
    );
    expect(
      ScriptReport.check(
        doc,
        [module(1, 7, seen: false)],
        radioReady: true,
        now: now,
      ).events.single.status,
      contains('not seen'),
    );
    expect(
      ScriptReport.check(
        doc,
        [module(1, 7)],
        radioReady: false,
        now: now,
      ).events.single.status,
      'Radio offline',
    );
  });
  test(
    'Saved readings do not expire; invalid timestamps and faults cannot pass',
    () {
      final doc = ScriptDocument.parse('show.csv', '${header}0,7,1,a\n');
      String status(FieldModule m) => ScriptReport.check(
        doc,
        [m],
        radioReady: true,
        now: now,
      ).events.single.status;
      expect(status(module(1, 7)..resistance = null), contains('unmeasured'));
      expect(
        status(
          module(1, 7)..measuredAt = now.subtract(const Duration(seconds: 31)),
        ),
        'Connected',
      );
      expect(
        status(
          module(1, 7)..measuredAt = now.subtract(const Duration(hours: 2)),
        ),
        'Connected',
      );
      expect(
        status(module(1, 7)..measuredAt = now.add(const Duration(seconds: 1))),
        contains('future'),
      );
      expect(
        status(module(1, 7)..error = 'query failed'),
        contains('query failed'),
      );
      expect(
        status(
          module(1, 7)
            ..resistance = const ResistanceSnapshot(4, true, [
              CueReading('Connected', '2 Ω'),
              CueReading('Open', ''),
              CueReading('Open', ''),
            ]),
        ),
        contains('Measurement warning'),
      );
      expect(
        status(
          module(1, 7)
            ..resistance = const ResistanceSnapshot(0, false, [
              CueReading('Raw', '12 mV'),
              CueReading('Open', ''),
              CueReading('Open', ''),
            ]),
        ),
        contains('Raw'),
      );
    },
  );
  test('Extra connected cues warn without masquerading as script events', () {
    final doc = ScriptDocument.parse('show.csv', '${header}0,7,2,a\n');
    final report = ScriptReport.check(
      doc,
      [module(1, 7)],
      radioReady: true,
      now: now,
    );
    expect(report.warnings, 1);
    expect(
      report.findings.where((f) => !f.error).single.message,
      contains('not used'),
    );
    expect(report.events.length, 1);
  });
}
