import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/settings/battery_settings.dart';
import 'package:nightfire_controller/widgets/settings_panel.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'module_diagnostics_test.dart' show FieldRadio;

void main() {
  testWidgets('the Settings switch turns automatic checks off and is saved', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final session = RadioSession(FieldRadio());
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: BatterySettingsScope(
            settings: BatterySettings(),
            child: ListenableBuilder(
              listenable: session,
              builder: (context, _) =>
                  ListView(children: [SettingsPanel(session: session)]),
            ),
          ),
        ),
      ),
    );
    final toggle = find.byKey(const ValueKey('auto-continuity'));
    expect(tester.widget<SwitchListTile>(toggle).value, isTrue);
    await tester.tap(toggle);
    await tester.pumpAndSettle();
    expect(session.autoContinuity, isFalse);
    expect(tester.widget<SwitchListTile>(toggle).value, isFalse);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getBool(RadioSession.autoContinuityKey), isFalse);
    session.dispose();
  });

  test('a new session loads the saved choice', () async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues({
      RadioSession.autoContinuityKey: false,
    });
    final session = RadioSession(FieldRadio());
    await Future<void>.delayed(Duration.zero);
    await Future<void>.delayed(Duration.zero);
    expect(session.autoContinuity, isFalse);
    session.dispose();
  });
}
