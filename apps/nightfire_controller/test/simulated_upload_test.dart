import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/scripts/script_document.dart';
import 'package:nightfire_controller/widgets/simulated_upload_panel.dart';

Future<void> openUpload(
  WidgetTester tester, {
  String? source,
  List<FieldModule> modules = const [],
}) async {
  tester.view.physicalSize = const Size(1280, 1000);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: SimulatedUploadPanel(
            script: ScriptDocument.parse(
              'demo.csv',
              source ??
                  '#Event Time,#Channel,#Cue,#Description\n0,1,1,A\n1,2,1,B\n',
            ),
            modules: modules,
            radioReady: true,
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Simulated upload'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'offline demo maps all script groups and never changes real module info',
    (tester) async {
      final module = FieldModule(99, 7, 24, 1);
      await openUpload(tester, modules: [module]);
      await tester.tap(find.text('Simulate upload'));
      await tester.pump();
      expect(find.textContaining('Demo module, group 1'), findsOneWidget);
      expect(find.textContaining('Demo module, group 2'), findsOneWidget);
      await tester.pump(const Duration(seconds: 3));
      expect(find.text('Complete (simulated)'), findsNWidgets(2));
      expect(
        find.text('0 simulated failures • no hardware changed.'),
        findsOneWidget,
      );
      expect(module.info, isEmpty);
      expect(module.scriptId, isNull);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'injected timeout is retained and retry completes only failed target',
    (tester) async {
      await openUpload(tester);
      void outcome(DemoUploadOutcome value) => tester
          .widget<DropdownButtonFormField<DemoUploadOutcome>>(
            find.byType(DropdownButtonFormField<DemoUploadOutcome>),
          )
          .onChanged!(value);
      outcome(DemoUploadOutcome.timeout);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Simulate upload'));
      await tester.pump();
      await tester.pump(const Duration(seconds: 3));
      expect(find.text('Failed (simulated)'), findsOneWidget);
      expect(find.text('Complete (simulated)'), findsOneWidget);
      expect(find.text('Demo timeout: no response simulated.'), findsOneWidget);
      outcome(DemoUploadOutcome.success);
      await tester.pumpAndSettle();
      expect(find.text('Demo timeout: no response simulated.'), findsOneWidget);
      await tester.tap(find.text('Retry failed simulation'));
      await tester.pump();
      expect(find.text('Complete (simulated)'), findsOneWidget);
      await tester.pump(const Duration(seconds: 2));
      expect(find.text('Complete (simulated)'), findsNWidgets(2));
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('cancel marks unfinished targets and disposing cancels timers', (
    tester,
  ) async {
    await openUpload(tester);
    await tester.tap(find.text('Simulate upload'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 350));
    await tester.tap(find.text('Cancel simulation'));
    await tester.pumpAndSettle();
    expect(find.text('Cancelled'), findsNWidgets(2));
    await tester.pump(const Duration(seconds: 3));
    expect(find.text('Complete (simulated)'), findsNothing);
    await tester.tap(find.text('Simulate upload'));
    await tester.pump();
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 3));
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'malformed scripts and missing discovered groups cannot pass validation',
    (tester) async {
      await openUpload(
        tester,
        source: '#Event Time,#Channel,#Cue,#Description\ninvalid,1,1,A\n',
      );
      OutlinedButton start() => tester.widget<OutlinedButton>(
        find.widgetWithText(OutlinedButton, 'Simulate upload'),
      );
      expect(start().onPressed, isNull);
      // Use a new widget state for a valid import, then choose real snapshots.
      await tester.pumpWidget(const SizedBox());
      await openUpload(tester);
      await tester.tap(find.byType(SwitchListTile));
      await tester.pumpAndSettle();
      expect(find.textContaining('Missing group 1'), findsOneWidget);
      expect(start().onPressed, isNull);
      expect(tester.takeException(), isNull);
    },
  );
}
