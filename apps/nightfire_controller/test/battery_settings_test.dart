import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/settings/battery_settings.dart';
import 'package:nightfire_controller/widgets/module_battery.dart';
import 'package:nightfire_controller/widgets/settings_panel.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'master_controller_test.dart' show MasterRadio;

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  group('battery settings', () {
    test('uses the module pack type unless overridden', () {
      final settings = BatterySettings();
      final info = {
        'App': 'field-module',
        'Battery mV': '11100',
        'Battery type': '3s',
      };
      var reading = settings.read(info);
      expect(reading.packType, '3S');
      expect(reading.emptyMv, 9600);
      expect(reading.fullMv, 12600);
      expect(reading.level, closeTo(0.5, 0.001));

      settings.setProfile('field-module', const BatteryProfile(packType: '4S'));
      reading = settings.read(info);
      expect(reading.packType, '4S');
      expect(reading.level, 0);
      // Another target is unaffected.
      expect(settings.read({...info, 'App': 'nrf-dummy'}).packType, '3S');
    });

    test('falls back to 2S when the pack type is missing or unknown', () {
      final settings = BatterySettings();
      expect(settings.read({'Battery mV': '7400'}).packType, '2S');
      expect(
        settings.read({'Battery mV': '7400', 'Battery type': 'LiFe'}).packType,
        '2S',
      );
    });

    test('applies scale and offset to the displayed voltage', () {
      final settings = BatterySettings()
        ..setProfile(
          'field-module',
          const BatteryProfile(scale: 1.05, offsetMv: -20),
        );
      final reading = settings.read({
        'App': 'field-module',
        'Battery mV': '8000',
      });
      expect(reading.reportedMv, 8000);
      expect(reading.millivolts, 8380);
      expect(reading.calibrated, isTrue);
      expect(
        settings.read({'App': 'field-module', 'Battery mV': '0'}).millivolts,
        isNull,
      );
    });

    test('calibration scale maps the module reading to the meter', () {
      expect(BatterySettings.calibrationScale(7800, 8034), closeTo(1.03, 1e-9));
      expect(
        () => BatterySettings.calibrationScale(7800, 3000),
        throwsFormatException,
      );
      expect(
        () => BatterySettings.calibrationScale(0, 8000),
        throwsFormatException,
      );
    });

    test('settings are saved and loaded per target', () async {
      BatterySettings().setProfile(
        'nrf-dummy',
        const BatteryProfile(packType: '1S', scale: 0.98, offsetMv: 15),
      );
      await Future<void>.delayed(Duration.zero);
      final loaded = await BatterySettings.load();
      expect(
        loaded.profileFor('nrf-dummy'),
        const BatteryProfile(packType: '1S', scale: 0.98, offsetMv: 15),
      );
      expect(loaded.profileFor('field-module'), const BatteryProfile());

      // Back to defaults removes the stored entry.
      loaded.setProfile('nrf-dummy', const BatteryProfile());
      await Future<void>.delayed(Duration.zero);
      expect((await BatterySettings.load()).customisedTargets, isEmpty);
    });

    test('bad stored values are ignored', () async {
      SharedPreferences.setMockInitialValues({
        BatterySettings.storageKey:
            '{"field-module":{"packType":"9X","scale":7,"offsetMv":"x"}}',
      });
      final loaded = await BatterySettings.load();
      expect(loaded.profileFor('field-module'), const BatteryProfile());
      SharedPreferences.setMockInitialValues({
        BatterySettings.storageKey: 'not json',
      });
      expect((await BatterySettings.load()).customisedTargets, isEmpty);
    });
  });

  testWidgets('battery gauge follows the settings scope', (tester) async {
    final settings = BatterySettings();
    await tester.pumpWidget(
      MaterialApp(
        home: BatterySettingsScope(
          settings: settings,
          child: const Scaffold(
            body: ModuleBatteryForInfo(
              info: {'App': 'field-module', 'Battery mV': '7400'},
            ),
          ),
        ),
      ),
    );
    expect(find.text('50% • 7.40 V'), findsOneWidget);
    settings.setProfile(
      'field-module',
      const BatteryProfile(packType: '2S', scale: 1.0, offsetMv: 200),
    );
    await tester.pump();
    expect(find.text('60% • 7.60 V'), findsOneWidget);
  });

  testWidgets('calibrate with meter sets the target scale', (tester) async {
    tester.view.physicalSize = const Size(1200, 2000);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final settings = BatterySettings();
    final session = RadioSession(MasterRadio());
    addTearDown(session.dispose);
    session.modules[0x11223344] = FieldModule(0x11223344, 1, 8, 1)
      ..info = {
        'App': 'field-module',
        'Battery mV': '8000',
        'Battery type': '2S',
      };
    await tester.pumpWidget(
      MaterialApp(
        home: BatterySettingsScope(
          settings: settings,
          child: Scaffold(
            body: SingleChildScrollView(child: SettingsPanel(session: session)),
          ),
        ),
      ),
    );
    expect(find.text('Field module'), findsOneWidget);
    expect(find.text('Dummy module'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('calibrate-field-module')));
    await tester.pumpAndSettle();
    expect(find.text('Module reading: 8.000 V'), findsOneWidget);
    await tester.enterText(find.byKey(const Key('meter-volts')), '8.2');
    await tester.tap(find.byKey(const Key('save-calibration')));
    await tester.pumpAndSettle();
    expect(settings.profileFor('field-module').scale, closeTo(1.025, 1e-9));
    expect(find.textContaining('×1.0250'), findsOneWidget);
    // Dummy modules have no reading, so their calibrate button is off.
    expect(
      tester
          .widget<ButtonStyleButton>(
            find.byKey(const ValueKey('calibrate-nrf-dummy')),
          )
          .onPressed,
      isNull,
    );
  });
}
