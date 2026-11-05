import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/widgets/field_modules_panel.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

void main() {
  testWidgets(
    'Scrolling and switching module tabs keeps expansion state separate from scroll offsets',
    (tester) async {
      tester.view.physicalSize = const Size(1280, 900);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final session = RadioSession(FieldRadio());
      session.modules[1] = FieldModule(1, 1, 24, 1)..lastSeen = DateTime.now();
      final bucket = PageStorageBucket();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: PageStorage(
              bucket: bucket,
              child: ListView(
                key: const PageStorageKey('modules'),
                padding: const EdgeInsets.all(24),
                children: [FieldModulesPanel(session: session)],
              ),
            ),
          ),
        ),
      );
      await tester.drag(find.byType(ListView), const Offset(0, -350));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<SegmentedButton<bool>>(find.byType(SegmentedButton<bool>))
            .selected,
        {false},
      );
      expect(find.textContaining('Last reply:'), findsNothing);
      await tester.ensureVisible(find.text('Settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      final headerLeft = tester.getTopLeft(find.text('Module 0x00000001')).dx;
      expect(
        tester.getTopLeft(find.byType(SegmentedButton<bool>)).dx,
        closeTo(headerLeft, 1),
      );
      expect(
        tester.getTopLeft(find.text('Module settings • 0x00000001')).dx,
        closeTo(headerLeft, 1),
      );
      await tester.tap(find.text('Status'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.text('Module details'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Module details'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Last reply:'), findsOneWidget);
      await tester.ensureVisible(find.text('Settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Status'));
      await tester.pumpAndSettle();
      expect(find.textContaining('Last reply:'), findsNothing);
      await tester.tap(find.text('Settings'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Module 0x00000001'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Module 0x00000001'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Module 0x00000001'));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<SegmentedButton<bool>>(find.byType(SegmentedButton<bool>))
            .selected,
        {false},
      );
      expect(find.textContaining('Last reply:'), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
      session.dispose();
    },
  );
}
