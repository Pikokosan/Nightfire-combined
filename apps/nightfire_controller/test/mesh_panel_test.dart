import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/controller/field_module.dart';
import 'package:nightfire_controller/controller/radio_session.dart';
import 'package:nightfire_controller/protocol/bridge_codec.dart';
import 'package:nightfire_controller/widgets/controller_navigation.dart';
import 'package:nightfire_controller/widgets/mesh_panel.dart';

import 'reliable_delivery_test.dart' show MeshBridge;

const near = 0xD96E30DD, far = 0x62A75130, behindStranger = 0x11110001;
const stranger = 0x5A000112;

FieldModule module(
  int uid, {
  ModuleRoute? route,
  int? relay,
  int? rssi,
  int? hops,
}) => FieldModule(uid, 1, 16, 0x10)
  ..seenInScan = true
  ..route = route
  ..relay = relay
  ..rssi = rssi
  ..hops = hops;

void main() {
  test(
    'topology: direct modules, one behind a relay, one behind an unknown relay',
    () {
      final topology = MeshTopology.from([
        module(near, route: ModuleRoute.direct, rssi: -58),
        module(far, route: ModuleRoute.relayed, relay: near, rssi: -60),
        module(behindStranger, route: ModuleRoute.relayed, relay: stranger),
      ]);
      expect(topology.node(near)!.column, 1);
      expect(topology.node(far)!.column, 2);
      expect(topology.node(behindStranger)!.column, 2);
      // A relay the app hasn't discovered is still drawn, off the bridge.
      expect(topology.node(stranger)!.module, isNull);
      expect(topology.node(stranger)!.column, 1);

      MeshLink linkTo(int uid) =>
          topology.links.singleWhere((link) => link.to == uid);
      expect(linkTo(near).from, isNull);
      expect(linkTo(near).kind, MeshLinkKind.direct);
      expect(linkTo(far).from, near);
      expect(linkTo(far).kind, MeshLinkKind.relayed);
      expect(linkTo(stranger).kind, MeshLinkKind.unknown);
    },
  );

  test('topology: flooded and not-yet-routed modules hang off the bridge', () {
    final topology = MeshTopology.from([
      module(near, route: ModuleRoute.flooded),
      module(far, hops: 0),
      module(stranger),
    ]);
    expect(topology.nodes.every((node) => node.column == 1), isTrue);
    final kinds = {for (final link in topology.links) link.to: link.kind};
    expect(kinds, {
      near: MeshLinkKind.flooded,
      far: MeshLinkKind.direct, // heard with no relay hops
      stranger: MeshLinkKind.unknown,
    });
    // Evenly spread down the column.
    expect(topology.nodes.map((node) => node.y).toSet().length, 3);
  });

  group('session route counts', () {
    const uid = 0x12ff5678;
    late MeshBridge bridge;
    late RadioSession session;
    setUp(() {
      bridge = MeshBridge();
      session = RadioSession(
        bridge,
        moduleTimeout: const Duration(milliseconds: 300),
      );
      session.ready = true;
      session.values['Firmware'] = 'usb-master-0.4.2';
      session.modules[uid] = FieldModule(uid, 1, 24, 0x20)..seenInScan = true;
    });
    tearDown(() => session.dispose());

    test(
      'each delivery is counted by route, and failures separately',
      () async {
        await session.readModule(uid);
        bridge.route = 0; // direct
        await session.readModule(uid);
        bridge.delivered = false;
        await session.readModule(uid);
        final module = session.modules[uid]!;
        expect(module.routeCounts, {
          ModuleRoute.relayed: 1,
          ModuleRoute.direct: 1,
        });
        expect(module.failedDeliveries, 1);
        expect(module.lastDeliveryFailed, isTrue);
        expect(module.attempts, 3);
        // The last route that worked is kept.
        expect(module.route, ModuleRoute.direct);
        bridge.delivered = true;
        await session.readModule(uid);
        expect(module.lastDeliveryFailed, isFalse);
        expect(module.attempts, 1);
      },
    );
  });

  testWidgets('the Mesh page is marked experimental and lists routes', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1600, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final session = RadioSession(MeshBridge());
    addTearDown(session.dispose);
    session.ready = true;
    session.values['Firmware'] = 'usb-master-0.4.2';
    session.modules[near] = module(
      near,
      route: ModuleRoute.direct,
      rssi: -58,
      hops: 0,
    )..routeCounts[ModuleRoute.direct] = 4;
    session.modules[far] =
        module(far, route: ModuleRoute.relayed, relay: near, rssi: -61, hops: 1)
          ..routeCounts[ModuleRoute.relayed] = 2
          ..failedDeliveries = 1;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(child: MeshPanel(session: session)),
        ),
      ),
    );
    expect(find.byKey(const ValueKey('mesh-experimental')), findsOneWidget);
    expect(find.text('Experimental'), findsOneWidget);
    expect(find.byKey(const ValueKey('mesh-topology')), findsOneWidget);
    expect(find.text('Via 0xD96E30DD'), findsOneWidget);
    expect(find.text('0xD96E30DD'), findsOneWidget); // the table row
    expect(find.text('-61 dBm'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('without mesh firmware the page says so', (tester) async {
    final session = RadioSession(MeshBridge());
    addTearDown(session.dispose);
    session.ready = true;
    session.values['Firmware'] = 'usb-master-0.4.1';
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(child: MeshPanel(session: session)),
        ),
      ),
    );
    expect(find.textContaining('need mesh bridge firmware'), findsOneWidget);
  });

  test('Mesh sits just before Settings in the navigation', () {
    final labels = [
      for (final (label, _, _) in ControllerNavigation.destinations) label,
    ];
    expect(labels.indexOf('Mesh'), labels.indexOf('Settings') - 1);
  });
}
