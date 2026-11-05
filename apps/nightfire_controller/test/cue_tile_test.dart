import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/protocol/nfcp_codec.dart';
import 'package:nightfire_controller/widgets/cue_tile.dart';

void main() {
  for (final scale in [1.0, 2.0]) {
    testWidgets('Cue size remains fixed for every state at text scale $scale', (
      tester,
    ) async {
      final readings = [
        const CueReading('Unknown', 'Not measured'),
        const CueReading('Connected', '1234.5 Ω'),
        const CueReading('Open', 'Open circuit'),
        const CueReading('Short', '0.0 Ω'),
        const CueReading('Raw', '65533 mV • uncalibrated'),
        const CueReading('Invalid', 'Not reported by legacy module'),
      ];
      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: MediaQueryData(textScaler: TextScaler.linear(scale)),
            child: Scaffold(
              body: SingleChildScrollView(
                child: Wrap(
                  children: [
                    for (var index = 0; index < readings.length; index++)
                      CueTile(number: index + 1, reading: readings[index]),
                  ],
                ),
              ),
            ),
          ),
        ),
      );
      for (final tile in find.byType(CueTile).evaluate()) {
        expect(
          tester.getSize(find.byWidget(tile.widget)),
          const Size(CueTile.tileWidth, CueTile.tileHeight),
        );
      }
      expect(tester.takeException(), isNull);
    });
  }
  testWidgets('Updating a reading preserves its tile dimensions', (
    tester,
  ) async {
    Future<void> render(CueReading reading) => tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Wrap(children: [CueTile(number: 64, reading: reading)]),
        ),
      ),
    );
    await render(const CueReading('Unknown', 'Not measured'));
    final before = tester.getSize(find.byType(CueTile));
    await render(const CueReading('Invalid', 'Not reported by legacy module'));
    expect(tester.getSize(find.byType(CueTile)), before);
    expect(tester.takeException(), isNull);
  });
}
