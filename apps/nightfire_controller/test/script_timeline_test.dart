import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/scripts/script_document.dart';
import 'package:nightfire_controller/widgets/script_timeline.dart';

CheckedScriptEvent event(int ms, int cue, {String status = 'Connected'}) =>
    CheckedScriptEvent(
      ScriptEvent(cue, '$ms', ms, 7, cue, 'Event $cue'),
      status,
      null,
    );

Future<void> showTimeline(
  WidgetTester tester,
  List<CheckedScriptEvent> events, {
  ValueChanged<ModuleFocus>? onOpenModule,
}) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: ScriptTimeline(events: events, onOpenModule: onOpenModule),
        ),
      ),
    ),
  );
}

void main() {
  testWidgets(
    'groups simultaneous cues and sorts preview without changing source',
    (tester) async {
      final events = [
        event(10000, 3),
        event(0, 1),
        event(0, 2, status: 'Short'),
      ];
      ModuleFocus? focus;
      await showTimeline(
        tester,
        events,
        onOpenModule: (value) => focus = value,
      );
      expect(find.text('00:00:00.000 • 2 cue events'), findsOneWidget);
      expect(find.text('Group 7 • Cue 1'), findsOneWidget);
      expect(find.text('Event 2\nShort'), findsOneWidget);
      await tester.tap(find.text('Group 7 • Cue 2'));
      expect(focus?.group, 7);
      expect(focus?.cue, 2);
      await tester.tap(find.byTooltip('Next scheduled time'));
      await tester.pumpAndSettle();
      expect(find.text('00:00:10.000 • 1 cue events'), findsOneWidget);
      expect(events.first.event.cue, 3);
      expect(
        tester
            .widget<IconButton>(
              find.byWidgetPredicate(
                (widget) =>
                    widget is IconButton &&
                    widget.tooltip == 'Next scheduled time',
              ),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.byTooltip('Previous scheduled time'));
      await tester.pumpAndSettle();
      expect(find.text('Group 7 • Cue 1'), findsOneWidget);
    },
  );

  testWidgets('scrubbing selects nearest actual timestamp and handles gaps', (
    tester,
  ) async {
    await showTimeline(tester, [
      event(1000, 1),
      event(9000, 2),
      event(300000, 3),
    ]);
    tester.widget<Slider>(find.byType(Slider)).onChanged!(8500);
    await tester.pumpAndSettle();
    expect(find.text('00:00:09.000 • 1 cue events'), findsOneWidget);
    tester.widget<Slider>(find.byType(Slider)).onChanged!(0);
    await tester.pumpAndSettle();
    expect(find.text('00:00:01.000 • 1 cue events'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('empty and zero-time scripts render safely on a phone', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await showTimeline(tester, []);
    expect(find.text('No valid events to preview.'), findsOneWidget);
    await showTimeline(tester, [event(0, 1)]);
    expect(tester.widget<Slider>(find.byType(Slider)).onChanged, isNull);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'large simultaneous groups are paged and time changes reset the page',
    (tester) async {
      await showTimeline(tester, [
        ...List.generate(60, (i) => event(0, i + 1)),
        event(1000, 61),
      ]);
      expect(find.text('1–25 of 60'), findsOneWidget);
      expect(find.byType(ListTile), findsNWidgets(25));
      await tester.tap(find.byTooltip('Next preview cues'));
      await tester.pumpAndSettle();
      expect(find.text('26–50 of 60'), findsOneWidget);
      await tester.tap(find.byTooltip('Next scheduled time'));
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Previous scheduled time'));
      await tester.pumpAndSettle();
      expect(find.text('1–25 of 60'), findsOneWidget);
    },
  );
}
