import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/widgets/offline_control_simulation.dart';

Future<void> openSimulation(WidgetTester tester) async {
  await tester.pumpWidget(
    MaterialApp(
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => const OfflineControlSimulation(),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'changing simulated arm source resets the demo without using hardware',
    (tester) async {
      await openSimulation(tester);
      await tester.tap(find.byType(SwitchListTile));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('demo-cue-1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Simulator settings'));
      await tester.pumpAndSettle();
      tester
          .widget<DropdownButtonFormField<SimulatedArmSource>>(
            find.byType(DropdownButtonFormField<SimulatedArmSource>),
          )
          .onChanged!(SimulatedArmSource.hardware);
      await tester.pumpAndSettle();
      expect(find.text('Simulated hardware input'), findsOneWidget);
      expect(find.text('Simulation: disarmed'), findsOneWidget);
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Simulate fire'),
            )
            .onPressed,
        isNull,
      );
      await tester.ensureVisible(find.text('Close simulation'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Close simulation'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'isolated simulation gates presses and blinks only last dummy cue',
    (tester) async {
      await openSimulation(tester);
      FilledButton fire() => tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Simulate fire'),
      );
      expect(fire().onPressed, isNull);
      await tester.tap(find.byKey(const ValueKey('demo-cue-1')));
      await tester.pumpAndSettle();
      expect(fire().onPressed, isNull);
      await tester.tap(find.byType(SwitchListTile));
      await tester.pumpAndSettle();
      expect(fire().onPressed, isNotNull);
      await tester.tap(find.text('Simulate fire'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      Opacity cue(int index) =>
          tester.widget<Opacity>(find.byKey(ValueKey('demo-opacity-$index')));
      expect(cue(1).opacity, 0.35);
      expect(cue(2).opacity, 1);
      expect(
        find.text('Last simulated: Demo cue 1 • no command sent'),
        findsOneWidget,
      );
      await tester.tap(find.byKey(const ValueKey('demo-cue-2')));
      await tester.pump();
      await tester.tap(find.text('Simulate fire'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(cue(1).opacity, 1);
      expect(cue(2).opacity, 0.35);
      await tester.tap(find.byType(SwitchListTile));
      await tester.pump();
      expect(fire().onPressed, isNull);
      await tester.pump(const Duration(seconds: 3));
      expect(cue(2).opacity, 1);
      await tester.tap(find.text('Close simulation'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'closing cancels pending animation and reopening starts disarmed',
    (tester) async {
      await openSimulation(tester);
      await tester.tap(find.byType(SwitchListTile));
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Simulate fire'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const ValueKey('demo-cue-1')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Simulate fire'));
      await tester.pump();
      await tester.tap(find.text('Close simulation'));
      await tester.pumpAndSettle();
      await tester.pump(const Duration(seconds: 4));
      await tester.tap(find.text('Open'));
      await tester.pumpAndSettle();
      expect(find.text('Simulation: disarmed'), findsOneWidget);
      expect(find.textContaining('Last simulated:'), findsNothing);
      await tester.tap(find.text('Close simulation'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );
}
