import 'package:flutter_test/flutter_test.dart';

/// Module cards start collapsed; open every one shown.
Future<void> openModules(WidgetTester tester) async {
  final headers = find.textContaining(RegExp(r'^Module 0x'));
  final names = [
    for (final element in headers.evaluate())
      (element.widget as dynamic).data as String,
  ];
  for (final name in names) {
    await tester.ensureVisible(find.text(name));
    await tester.pumpAndSettle();
    await tester.tap(find.text(name));
    await tester.pumpAndSettle();
  }
}
