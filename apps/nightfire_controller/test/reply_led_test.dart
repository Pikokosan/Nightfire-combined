import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/widgets/field_modules_panel.dart';

void main() {
  testWidgets('a new reply lights the LED green, then it fades to dark', (
    tester,
  ) async {
    Widget led(int flash) => MaterialApp(
      home: Center(child: ReplyLed(flash: flash)),
    );
    Color color() =>
        (tester.widget<Container>(find.byType(Container)).decoration!
                as BoxDecoration)
            .color!;
    await tester.pumpWidget(led(0));
    final dark = color();
    await tester.pumpWidget(led(1));
    await tester.pump(const Duration(milliseconds: 16));
    final lit = color();
    expect(lit.g, greaterThan(lit.r));
    expect(lit.g, greaterThan(lit.b));
    expect(lit, isNot(dark));
    await tester.pumpAndSettle();
    expect(color(), dark);
  });

  testWidgets('a check with no reply shows a steady red light', (tester) async {
    Widget led({required bool failed}) => MaterialApp(
      home: Center(child: ReplyLed(flash: 0, failed: failed)),
    );
    Color color() =>
        (tester.widget<Container>(find.byType(Container)).decoration!
                as BoxDecoration)
            .color!;
    await tester.pumpWidget(led(failed: true));
    await tester.pumpAndSettle();
    final red = color();
    expect(red.r, greaterThan(red.g));
    expect(red.r, greaterThan(red.b));
    await tester.pumpWidget(led(failed: false));
    await tester.pumpAndSettle();
    expect(color(), isNot(red));
  });
}
