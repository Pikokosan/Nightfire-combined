import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/settings/battery_settings.dart';
import 'package:nightfire_controller/widgets/module_battery.dart';
import 'package:shared_preferences/shared_preferences.dart';

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
}
