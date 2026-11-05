import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/main.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/widgets/field_modules_panel.dart';
import 'package:nightfire_controller/widgets/controller_navigation.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

void main() {
  testWidgets(
    'Dashboard searches modules and filters cues without renumbering',
    (tester) async {
      final session = RadioSession(FieldRadio());
      session.modules[1] = FieldModule(1, 2, 4, 1)
        ..resistance = const ResistanceSnapshot(0, true, [
          CueReading('Connected', '1.2 Ω'),
          CueReading('Open', 'Open circuit'),
          CueReading('Short', '0.0 Ω'),
          CueReading('Invalid', 'No valid measurement'),
        ]);
      session.modules[2] = FieldModule(2, 3, 2, 1);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: FieldModulesPanel(session: session),
            ),
          ),
        ),
      );
      expect(find.text('2 modules • 6 cues'), findsOneWidget);
      expect(find.text('2 Unmeasured'), findsNWidgets(2));
      expect(
        find.text('Radio disconnected • saved readings are not current.'),
        findsOneWidget,
      );
      await tester.ensureVisible(find.widgetWithText(ChoiceChip, 'Open'));
      await tester.tap(find.widgetWithText(ChoiceChip, 'Open'));
      await tester.pumpAndSettle();
      expect(find.text('Cue 2 • Open'), findsOneWidget);
      expect(find.text('Cue 1 • Connected'), findsNothing);
      expect(find.text('No cues match this filter.'), findsOneWidget);
      await tester.ensureVisible(
        find.widgetWithText(ChoiceChip, 'Short / invalid'),
      );
      await tester.tap(find.widgetWithText(ChoiceChip, 'Short / invalid'));
      await tester.pumpAndSettle();
      expect(find.text('Cue 3 • Short'), findsOneWidget);
      expect(find.text('Cue 4 • Invalid'), findsOneWidget);
      expect(find.text('Cue 2 • Open'), findsNothing);
      await tester.enterText(
        find.byKey(const ValueKey('module-search')),
        'GROUP 3',
      );
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('module-card-1')), findsNothing);
      expect(find.byKey(const ValueKey('module-card-2')), findsOneWidget);
      await tester.enterText(
        find.byKey(const ValueKey('module-search')),
        'missing',
      );
      await tester.pumpAndSettle();
      expect(find.text('No modules match your search.'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );

  for (final size in [const Size(360, 800), const Size(1280, 800)]) {
    testWidgets('64 cue module fits ${size.width.toInt()} pixel viewport', (
      tester,
    ) async {
      tester.view.physicalSize = size;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final session = RadioSession(FieldRadio());
      final module = FieldModule(1, 1, 64, 1)
        ..seenInScan = true
        ..measuredAt = DateTime.now()
        ..resistance = ResistanceSnapshot(
          0,
          true,
          List.generate(64, (_) => const CueReading('Connected', '2.5 Ω')),
        );
      session.modules[1] = module;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: FieldModulesPanel(session: session),
            ),
          ),
        ),
      );
      expect(find.text('Cue 64 • Connected'), findsOneWidget);
      expect(find.textContaining('Group 1 • Script: Not loaded'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    });
  }
  for (final size in [const Size(360, 800), const Size(1280, 800)]) {
    testWidgets(
      'Navigation fits ${size.width.toInt()} and keeps arm status visible',
      (tester) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        await tester.pumpWidget(const NightfireApp());
        expect(find.text('ARM STATE UNKNOWN'), findsOneWidget);
        expect(find.text('Command module'), findsOneWidget);
        expect(
          find.byType(size.width >= 900 ? NavigationRail : NavigationBar),
          findsOneWidget,
        );
        await tester.tap(find.text('Radio'));
        await tester.pumpAndSettle();
        expect(find.text('Radio connection'), findsOneWidget);
        expect(find.text('ARM STATE UNKNOWN'), findsOneWidget);
        await tester.tap(find.text('Scripts'));
        await tester.pumpAndSettle();
        expect(find.text('Script workspace'), findsOneWidget);
        expect(find.text('ARM STATE UNKNOWN'), findsOneWidget);
        await tester.tap(find.text('Interface'));
        await tester.pumpAndSettle();
        expect(find.text('Hardware status'), findsOneWidget);
        expect(find.text('ARM STATE UNKNOWN'), findsOneWidget);
        await tester.tap(find.text('Modules'));
        await tester.pumpAndSettle();
        expect(find.text('Command module'), findsOneWidget);
        expect(tester.takeException(), isNull);
      },
    );
  }

  testWidgets('Switching pages preserves dashboard filters and readings', (
    tester,
  ) async {
    final session = RadioSession(FieldRadio());
    session.modules[1] = FieldModule(1, 1, 2, 1)
      ..resistance = const ResistanceSnapshot(0, true, [
        CueReading('Connected', '2 Ω'),
        CueReading('Short', '0 Ω'),
      ]);
    var selected = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) => ControllerNavigation(
              selectedIndex: selected,
              onSelected: (index) => setState(() => selected = index),
              pages: [
                SingleChildScrollView(
                  child: FieldModulesPanel(session: session),
                ),
                const Text('Manual fire workspace'),
                const Text('Script workspace'),
                const Text('Radio settings'),
                const Text('Interface settings'),
              ],
            ),
          ),
        ),
      ),
    );
    await tester.ensureVisible(
      find.widgetWithText(ChoiceChip, 'Short / invalid'),
    );
    await tester.tap(find.widgetWithText(ChoiceChip, 'Short / invalid'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Radio'));
    await tester.pumpAndSettle();
    expect(find.text('Radio settings'), findsOneWidget);
    await tester.tap(find.text('Modules'));
    await tester.pumpAndSettle();
    expect(find.text('Cue 2 • Short'), findsOneWidget);
    expect(find.text('Cue 1 • Connected'), findsNothing);
    expect(session.modules[1]!.resistance, isNotNull);
    await tester.pumpWidget(const SizedBox());
    session.dispose();
  });
}
