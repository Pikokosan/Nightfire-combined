import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/main.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/scripts/script_document.dart';
import 'package:nightfire_controller/widgets/cue_tile.dart';
import 'package:nightfire_controller/widgets/field_modules_panel.dart';
import 'package:nightfire_controller/widgets/scripts_panel.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

void main() {
  testWidgets(
    'Script error opens its module and unload restores normal cue colors',
    (tester) async {
      tester.view.physicalSize = const Size(1280, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        MaterialApp(
          home: RadioPage(
            transport: FieldRadio(),
            pickScript: () async => const ScriptSource(
              'show.csv',
              '#Event Time,#Channel,#Cue,#Description\n0,1,2,Example\n',
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Radio'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Scripts'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Check connected modules'));
      await tester.pumpAndSettle();
      final error = find.textContaining('Channel 1, cue 2: Short');
      await tester.ensureVisible(error);
      await tester.pumpAndSettle();
      await tester.tap(error);
      await tester.pumpAndSettle();
      expect(find.text('Module 0x12FF5678'), findsOneWidget);
      final unused = tester.widget<CueTile>(
        find.byKey(ValueKey('cue-${FieldRadio.uid}-0')),
      );
      expect(unused.color, Colors.orange);
      expect(find.text('1 Not in script'), findsNWidgets(2));
      final selected = tester.widget<CueTile>(
        find.byKey(ValueKey('cue-${FieldRadio.uid}-1')),
      );
      expect(selected.focused, isTrue);
      expect(selected.color, Colors.redAccent);
      expect(
        tester.getSize(find.byKey(ValueKey('cue-${FieldRadio.uid}-0'))),
        const Size(145, 104),
      );
      await tester.tap(find.text('Module 0x12FF5678'));
      await tester.pumpAndSettle();
      final card = find.byKey(ValueKey('module-card-${FieldRadio.uid}'));
      for (final label in [
        '1 Connected',
        '1 Open',
        '1 Short',
        '1 Not in script',
        '1 Invalid',
      ]) {
        expect(
          find.descendant(of: card, matching: find.text(label)),
          findsOneWidget,
        );
      }
      expect(find.text('Cue 1 • Connected'), findsNothing);
      await tester.tap(find.text('Scripts'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Unload'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Unload'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Modules'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Module 0x12FF5678'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Module 0x12FF5678'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<CueTile>(find.byKey(ValueKey('cue-${FieldRadio.uid}-0')))
            .color,
        Colors.lightGreen,
      );
      expect(find.text('1 Not in script'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    },
  );

  for (final width in [360.0, 1280.0]) {
    testWidgets('Collapsed header keeps counts visible at width $width', (
      tester,
    ) async {
      tester.view.physicalSize = Size(width, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final session = RadioSession(FieldRadio());
      session.modules[1] = FieldModule(1, 1, 3, 1)
        ..resistance = const ResistanceSnapshot(0, true, [
          CueReading('Connected', '2 Ω'),
          CueReading('Open', 'Open'),
          CueReading('Short', '0 Ω'),
        ]);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: FieldModulesPanel(session: session),
            ),
          ),
        ),
      );
      await tester.ensureVisible(find.text('Module 0x00000001'));
      await tester.tap(find.text('Module 0x00000001'));
      await tester.pumpAndSettle();
      final card = find.byKey(const ValueKey('module-card-1'));
      expect(
        find.descendant(of: card, matching: find.text('1 Connected')),
        findsOneWidget,
      );
      expect(
        find.descendant(of: card, matching: find.text('1 Open')),
        findsOneWidget,
      );
      expect(
        find.descendant(of: card, matching: find.text('1 Short')),
        findsOneWidget,
      );
      expect(find.text('Cue 1 • Connected'), findsNothing);
      if (width > 900) {
        expect(
          tester
              .getTopLeft(
                find.descendant(of: card, matching: find.text('1 Connected')),
              )
              .dx,
          greaterThan(tester.getTopRight(find.text('Module 0x00000001')).dx),
        );
      }
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    });
  }

  testWidgets(
    'Opening a missing group clears search and explains missing hardware',
    (tester) async {
      final session = RadioSession(FieldRadio());
      session.modules[1] = FieldModule(1, 1, 1, 1);
      ModuleFocus? focus;
      late StateSetter update;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: StatefulBuilder(
                builder: (context, setState) {
                  update = setState;
                  return FieldModulesPanel(session: session, focus: focus);
                },
              ),
            ),
          ),
        ),
      );
      await tester.enterText(
        find.byKey(const ValueKey('module-search')),
        'hidden',
      );
      update(() => focus = const ModuleFocus(group: 9, cue: 2));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<TextField>(find.byKey(const ValueKey('module-search')))
            .controller!
            .text,
        isEmpty,
      );
      expect(
        find.text(
          'The module referenced by this script finding is not currently discovered.',
        ),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('module-card-1')), findsNothing);
      await tester.tap(find.text('Show all modules'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('module-card-1')), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );

  test('Structured findings preserve group, module and cue targets', () {
    final module = FieldModule(42, 7, 2, 1)
      ..seenInScan = true
      ..measuredAt = DateTime.now()
      ..resistance = const ResistanceSnapshot(0, true, [
        CueReading('Short', '0 Ω'),
        CueReading('Connected', '2 Ω'),
      ]);
    final report = ScriptReport.check(
      ScriptDocument.parse(
        'test.csv',
        '#Event Time,#Channel,#Cue,#Description\n0,7,1,A\n',
      ),
      [module],
      radioReady: true,
    );
    expect(report.findings.first.focus.uid, 42);
    expect(report.findings.first.focus.group, 7);
    expect(report.findings.first.focus.cue, 1);
    expect(report.findings.last.focus.cue, 2);
  });
}
