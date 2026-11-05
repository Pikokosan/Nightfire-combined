import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/widgets/scripts_panel.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

void main() {
  testWidgets(
    'Saved readings persist while changed continuity updates findings without rendering failure',
    (tester) async {
      final session = RadioSession(FieldRadio())..ready = true;
      final module = FieldModule(1, 7, 7, 1)
        ..seenInScan = true
        ..measuredAt = DateTime.now()
        ..resistance = ResistanceSnapshot(
          0,
          true,
          List.filled(7, const CueReading('Connected', '2 Ω')),
        );
      session.modules[1] = module;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: AnimatedBuilder(
                animation: session,
                builder: (context, _) => ScriptsPanel(
                  session: session,
                  pickScript: () async => const ScriptSource(
                    'test.csv',
                    '#Event Time,#Channel,#Cue,#Description\n0,7,1,A\n1,7,2,B\n2,7,3,C\n',
                  ),
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      expect(find.text('0 errors • 4 warnings'), findsOneWidget);
      for (var repeat = 0; repeat < 3; repeat++) {
        module.measuredAt = DateTime.now().subtract(const Duration(hours: 2));
        session.notifyListeners();
        await tester.pump(const Duration(seconds: 1));
        expect(find.text('0 errors • 4 warnings'), findsOneWidget);
        module.resistance = ResistanceSnapshot(
          0,
          true,
          List.generate(
            7,
            (index) => index < 3
                ? const CueReading('Open', 'Open circuit')
                : const CueReading('Connected', '2 Ω'),
          ),
        );
        session.notifyListeners();
        await tester.pumpAndSettle();
        expect(find.text('3 errors • 4 warnings'), findsOneWidget);
        module.resistance = ResistanceSnapshot(
          0,
          true,
          List.filled(7, const CueReading('Connected', '2 Ω')),
        );
        session.notifyListeners();
        await tester.pumpAndSettle();
        expect(find.text('0 errors • 4 warnings'), findsOneWidget);
        expect(tester.takeException(), isNull);
      }
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );

  testWidgets(
    'Check connected modules runs discovery then continuity using diagnostic queries only',
    (tester) async {
      final radio = FieldRadio();
      final session = RadioSession(
        radio,
        discoveryRounds: 1,
        discoveryInterval: Duration.zero,
      );
      await tester.runAsync(() => session.connect('field'));
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: ScriptsPanel(
                session: session,
                pickScript: () async => const ScriptSource(
                  'test.csv',
                  '#Event Time,#Channel,#Cue,#Description\n0,1,1,Example\n',
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      expect(radio.queryCommands, isEmpty);
      await tester.tap(find.text('Check connected modules'));
      await tester.pumpAndSettle();
      expect(radio.queryCommands, [0x40, 0x52, 0x24]);
      expect(find.text('0 errors • 0 warnings'), findsOneWidget);
      expect(session.modules[FieldRadio.uid]!.resistance, isNotNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );

  testWidgets(
    'Import checks update with hardware and failed replacement clears old report',
    (tester) async {
      tester.view.physicalSize = const Size(360, 800);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final session = RadioSession(FieldRadio())..ready = true;
      session.values['Firmware'] = 'usb-monitor-0.2';
      session.modules[1] = FieldModule(1, 7, 1, 1)
        ..seenInScan = true
        ..measuredAt = DateTime.now()
        ..resistance = const ResistanceSnapshot(0, true, [
          CueReading('Connected', '2 Ω'),
        ]);
      ScriptSource? source = const ScriptSource(
        'sample.csv',
        '#Event Time,#Channel,#Cue,#Description\n0,7,1,Test\n',
      );
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: AnimatedBuilder(
                animation: session,
                builder: (context, _) => ScriptsPanel(
                  session: session,
                  pickScript: () async => source,
                ),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      expect(find.text('0 errors • 0 warnings'), findsOneWidget);
      expect(find.text('sample.csv'), findsNWidgets(2));
      session.modules[1]!.measuredAt = DateTime.now().subtract(
        const Duration(seconds: 31),
      );
      session.notifyListeners();
      await tester.pump(const Duration(seconds: 1));
      expect(find.text('0 errors • 0 warnings'), findsOneWidget);
      expect(find.textContaining('Measured:'), findsOneWidget);
      session.modules[1]!.measuredAt = DateTime.now();
      session.notifyListeners();
      await tester.pumpAndSettle();
      source = null;
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      expect(find.text('0 errors • 0 warnings'), findsOneWidget);
      session.modules[1]!.seenInScan = false;
      session.notifyListeners();
      await tester.pumpAndSettle();
      expect(find.text('1 errors • 0 warnings'), findsOneWidget);
      session.ready = false;
      session.notifyListeners();
      await tester.pumpAndSettle();
      expect(
        find.text(
          'Radio offline • reconnect to check hardware.',
        ),
        findsOneWidget,
      );
      source = const ScriptSource(
        'bad.csv',
        '#Event Time,#Channel,#Cue,#Description\n0,1,1,"bad',
      );
      await tester.tap(find.text('Load script'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Import failed:'), findsOneWidget);
      expect(find.text('sample.csv'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );
}
