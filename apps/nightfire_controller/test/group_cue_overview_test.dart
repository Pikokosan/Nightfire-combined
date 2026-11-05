import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/widgets/cue_tile.dart';
import 'package:nightfire_controller/widgets/group_cue_overview.dart';

Future<void> showOverview(WidgetTester tester, List<FieldModule> modules) =>
    tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: GroupCueOverview(modules: modules, radioReady: false),
          ),
        ),
      ),
    );

void main() {
  testWidgets(
    'preview blinks only the last previewed cue and preserves tile size',
    (tester) async {
      await showOverview(tester, [FieldModule(1, 7, 2, 1)]);
      await tester.tap(find.byKey(const ValueKey('preview-select-1-0')));
      await tester.pump();
      await tester.ensureVisible(find.text('Preview cue'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Preview cue'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      FadeTransition pulse(int index) => tester.widget<FadeTransition>(
        find.byKey(ValueKey('preview-pulse-1-$index')),
      );
      expect(pulse(0).opacity.value, closeTo(0.35, 0.01));
      expect(pulse(1).opacity.value, 1);
      expect(tester.getSize(find.byType(CueTile).first), const Size(145, 104));
      await tester.pumpAndSettle();
      expect(pulse(0).opacity.value, 1);
      expect(
        find.text('Last preview: Module 0x00000001 • Cue 1 • no command sent'),
        findsOneWidget,
      );
      await tester.ensureVisible(
        find.byKey(const ValueKey('preview-select-1-1')),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('preview-select-1-1')));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Preview cue'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Preview cue'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(pulse(0).opacity.value, 1);
      expect(pulse(1).opacity.value, closeTo(0.35, 0.01));
      expect(
        find.text('Last preview: Module 0x00000001 • Cue 2 • no command sent'),
        findsOneWidget,
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'selected group shows every matching module and all continuity states',
    (tester) async {
      final a = FieldModule(1, 7, 4, 1)
        ..resistance = const ResistanceSnapshot(0, true, [
          CueReading('Connected', '2 Ω'),
          CueReading('Open', 'Open circuit'),
          CueReading('Short', '0 Ω'),
        ]);
      final b = FieldModule(2, 7, 1, 1);
      final c = FieldModule(3, 9, 2, 1);
      await showOverview(tester, [c, b, a]);
      expect(find.text('Module 0x00000001'), findsOneWidget);
      expect(find.text('Module 0x00000002'), findsOneWidget);
      expect(find.text('Module 0x00000003'), findsNothing);
      expect(find.byType(CueTile), findsNWidgets(5));
      for (final label in ['1 Connected', '1 Open', '1 Short']) {
        expect(find.text(label), findsOneWidget);
      }
      expect(
        find.text('Radio disconnected • showing saved continuity.'),
        findsOneWidget,
      );
      tester
          .widget<DropdownButtonFormField<int>>(
            find.byType(DropdownButtonFormField<int>),
          )
          .onChanged!(9);
      await tester.pumpAndSettle();
      expect(find.text('Module 0x00000003'), findsOneWidget);
      expect(find.text('Module 0x00000001'), findsNothing);
      expect(find.byType(CueTile), findsNWidgets(2));
      // A settings/discovery update must not silently switch the chosen group.
      c.group = 10;
      await showOverview(tester, [a, b, c]);
      expect(
        find.text('Group 9 is no longer available. Select another group.'),
        findsOneWidget,
      );
      expect(find.byType(CueTile), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('phone retains fixed cue dimensions and updates saved readings', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final module = FieldModule(1, 0, 64, 1);
    await showOverview(tester, [module]);
    expect(find.byType(CueTile), findsNWidgets(64));
    expect(tester.getSize(find.byType(CueTile).first), const Size(145, 104));
    expect(find.text('64 Unmeasured'), findsOneWidget);
    module.resistance = ResistanceSnapshot(
      4,
      true,
      List.filled(64, const CueReading('Connected', '3 Ω')),
    );
    await showOverview(tester, [module]);
    expect(find.text('64 Connected'), findsOneWidget);
    expect(
      find.text('Measurement warning: Measurement fault detected'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });

  testWidgets('empty discovery explains how to obtain snapshots', (
    tester,
  ) async {
    await showOverview(tester, []);
    expect(find.textContaining('No modules discovered.'), findsOneWidget);
    expect(find.byType(CueTile), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
