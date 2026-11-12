import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/main.dart';
import 'package:nightfire_controller/widgets/scripts_panel.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

class PickerRadio extends FieldRadio {
  int disconnects = 0;
  int connects = 0;
  @override
  Future<void> connect(String id) async {
    connects++;
  }

  @override
  Future<void> disconnect() async {
    disconnects++;
  }
}

/// Walks the lifecycle through the states Android really reports (resumed,
/// inactive, hidden, paused); text fields' lifecycle listeners assert on
/// skipped steps.
void moveTo(WidgetTester tester, AppLifecycleState target) {
  const order = [
    AppLifecycleState.resumed,
    AppLifecycleState.inactive,
    AppLifecycleState.hidden,
    AppLifecycleState.paused,
  ];
  final current = tester.binding.lifecycleState;
  if (current == null || !order.contains(current)) {
    tester.binding.handleAppLifecycleStateChanged(target);
    return;
  }
  var index = order.indexOf(current);
  final end = order.indexOf(target);
  while (index != end) {
    index += end > index ? 1 : -1;
    tester.binding.handleAppLifecycleStateChanged(order[index]);
  }
}

void main() {
  testWidgets(
    'recovers a transport lost while picker is open after activity resumes',
    (tester) async {
      moveTo(tester, AppLifecycleState.resumed);
      final radio = PickerRadio();
      final picker = Completer<ScriptSource?>();
      await tester.pumpWidget(
        MaterialApp(
          home: RadioPage(transport: radio, pickScript: () => picker.future),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('Radio'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Connect'));
      await tester.pumpAndSettle();
      expect(radio.connects, 1);
      await tester.tap(find.text('Scripts'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Load script'));
      await tester.pump();
      moveTo(tester, AppLifecycleState.paused);
      radio.stream.addError(StateError('Test background socket abort'));
      await tester.pump();
      picker.complete(null);
      await tester.pump();
      expect(radio.connects, 1);
      moveTo(tester, AppLifecycleState.resumed);
      await tester.pumpAndSettle();
      expect(radio.connects, 2);
      // Subsequent ordinary resume events must not reconnect again.
      moveTo(tester, AppLifecycleState.paused);
      await tester.pump();
      moveTo(tester, AppLifecycleState.resumed);
      await tester.pumpAndSettle();
      expect(radio.connects, 2);
      await tester.pumpWidget(const SizedBox());
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );

  for (final resultBeforeResume in [true, false]) {
    for (final outcome in ['selected', 'cancelled', 'error']) {
      testWidgets(
        'picker $outcome, result before resume=$resultBeforeResume preserves USB then restores background disconnect',
        (tester) async {
          moveTo(tester, AppLifecycleState.resumed);
          final radio = PickerRadio();
          final picker = Completer<ScriptSource?>();
          await tester.pumpWidget(
            MaterialApp(
              home: RadioPage(
                transport: radio,
                pickScript: () => picker.future,
              ),
            ),
          );
          await tester.pumpAndSettle();
          await tester.tap(find.text('Radio'));
          await tester.pumpAndSettle();
          await tester.tap(find.text('Connect'));
          await tester.pumpAndSettle();
          final baseline = radio.disconnects;
          await tester.tap(find.text('Scripts'));
          await tester.pumpAndSettle();
          await tester.tap(find.text('Load script'));
          await tester.pump();
          moveTo(tester, AppLifecycleState.paused);
          await tester.pump();
          expect(radio.disconnects, baseline);
          if (!resultBeforeResume) {
            moveTo(tester, AppLifecycleState.resumed);
          }
          if (outcome == 'error') {
            picker.completeError(const FormatException('Test picker error'));
          } else {
            picker.complete(
              outcome == 'cancelled'
                  ? null
                  : const ScriptSource(
                      'picker.csv',
                      '#Event Time,#Channel,#Cue,#Description\n0,1,1,Example\n',
                    ),
            );
          }
          await tester.pump();
          if (resultBeforeResume) {
            // Completing the result must not re-enable a disconnect while the
            // activity is still returning from the picker.
            moveTo(tester, AppLifecycleState.paused);
            await tester.pump();
            expect(radio.disconnects, baseline);
            moveTo(tester, AppLifecycleState.resumed);
          }
          await tester.pumpAndSettle();
          expect(radio.disconnects, baseline);
          if (outcome == 'selected') {
            expect(find.text('picker.csv'), findsNWidgets(2));
          }
          if (outcome == 'error') {
            expect(find.textContaining('Test picker error'), findsOneWidget);
          }
          moveTo(tester, AppLifecycleState.paused);
          await tester.pump();
          expect(radio.disconnects, baseline + 1);
          moveTo(tester, AppLifecycleState.resumed);
          await tester.pumpWidget(const SizedBox());
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        },
      );
    }
  }
}
