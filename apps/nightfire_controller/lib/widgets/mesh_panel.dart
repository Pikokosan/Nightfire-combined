import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../controller/field_module.dart';
import '../controller/radio_session.dart';
import '../protocol/bridge_codec.dart';

/// How a module was last reached, as drawn on the Mesh page.
enum MeshLinkKind { direct, relayed, flooded, unknown }

class MeshNode {
  MeshNode(this.uid, this.column, {this.module});
  final int uid;

  /// 1: heard straight from the bridge (or not known); 2: through a relay.
  final int column;

  /// Null for a relay the bridge named that this app hasn't discovered.
  final FieldModule? module;
  double y = 0.5;

  bool get failed =>
      module != null && (module!.lastDeliveryFailed || module!.noReply);
  String get id => uid.toRadixString(16).padLeft(8, '0').toUpperCase();
}

class MeshLink {
  const MeshLink(this.from, this.to, this.kind, {this.rssi});

  /// Null for the bridge.
  final int? from;
  final int to;
  final MeshLinkKind kind;
  final int? rssi;
}

/// Who reaches whom, from what the bridge reported for each module's last
/// delivered request (the route its reply took) and the hop count of the
/// last frame heard.
class MeshTopology {
  MeshTopology(this.nodes, this.links);
  final List<MeshNode> nodes;
  final List<MeshLink> links;

  factory MeshTopology.from(Iterable<FieldModule> modules) {
    final byUid = {for (final module in modules) module.uid: module};
    final nodes = <int, MeshNode>{};
    final links = <MeshLink>[];
    final sorted = byUid.values.toList()
      ..sort((a, b) => a.uid.compareTo(b.uid));
    for (final module in sorted) {
      final relay = module.route == ModuleRoute.relayed ? module.relay : null;
      if (relay != null && relay != module.uid) {
        nodes[module.uid] = MeshNode(module.uid, 2, module: module);
        nodes.putIfAbsent(
          relay,
          () => MeshNode(relay, 1, module: byUid[relay]),
        );
        links.add(
          MeshLink(relay, module.uid, MeshLinkKind.relayed, rssi: module.rssi),
        );
        continue;
      }
      nodes.putIfAbsent(
        module.uid,
        () => MeshNode(module.uid, 1, module: module),
      );
      final kind = switch (module.route) {
        ModuleRoute.direct => MeshLinkKind.direct,
        ModuleRoute.flooded => MeshLinkKind.flooded,
        _ => module.hops == 0 ? MeshLinkKind.direct : MeshLinkKind.unknown,
      };
      links.add(MeshLink(null, module.uid, kind, rssi: module.rssi));
    }
    // Relays the app never heard from still hang off the bridge.
    for (final node in nodes.values) {
      if (node.column == 1 && !links.any((link) => link.to == node.uid)) {
        links.add(MeshLink(null, node.uid, MeshLinkKind.unknown));
      }
    }
    final topology = MeshTopology(nodes.values.toList(), links);
    topology._layout();
    return topology;
  }

  int get rows => math.max(
    1,
    math.max(
      nodes.where((node) => node.column == 1).length,
      nodes.where((node) => node.column == 2).length,
    ),
  );

  MeshNode? node(int uid) => nodes.where((node) => node.uid == uid).firstOrNull;

  /// Spreads each column evenly; relayed modules sit in the order of their
  /// relays, so their links don't cross more than needed.
  void _layout() {
    final first = nodes.where((node) => node.column == 1).toList()
      ..sort((a, b) => a.uid.compareTo(b.uid));
    _spread(first);
    double relayY(MeshNode node) {
      final link = links.where((link) => link.to == node.uid).firstOrNull;
      return link?.from == null ? 0 : (this.node(link!.from!)?.y ?? 0);
    }

    final second = nodes.where((node) => node.column == 2).toList()
      ..sort((a, b) {
        final order = relayY(a).compareTo(relayY(b));
        return order != 0 ? order : a.uid.compareTo(b.uid);
      });
    _spread(second);
  }

  static void _spread(List<MeshNode> column) {
    for (var i = 0; i < column.length; i++) {
      column[i].y = (i + 1) / (column.length + 1);
    }
  }
}

Color signalColor(int? rssi, ColorScheme scheme) {
  if (rssi == null) return scheme.outline;
  if (rssi >= -70) return const Color(0xFF4CE65A);
  if (rssi >= -85) return const Color(0xFFFFC14D);
  return const Color(0xFFFF4D4D);
}

/// The experimental Mesh page: the route each module was last reached by.
class MeshPanel extends StatelessWidget {
  const MeshPanel({super.key, required this.session});
  final RadioSession? session;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final session = this.session;
    final modules =
        session?.modules.values
            .where((module) => module.seenInScan || module.lastSeen != null)
            .toList() ??
        const <FieldModule>[];
    final mesh =
        session != null && supportsReliableDelivery(session.values['Firmware']);
    final topology = MeshTopology.from(modules);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 12,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            Text('Mesh', style: theme.textTheme.headlineLarge),
            Chip(
              key: const ValueKey('mesh-experimental'),
              avatar: const Icon(Icons.science_outlined, size: 18),
              label: const Text('Experimental'),
              side: const BorderSide(color: Color(0xFFFFC14D)),
              labelStyle: const TextStyle(color: Color(0xFFFFC14D)),
            ),
          ],
        ),
        const SizedBox(height: 4),
        const Text(
          'How the bridge last reached each module: the path its latest reply '
          'took. A module behind a relay hangs off that relay. Routes update '
          'with every request, including the automatic continuity checks.',
        ),
        if (session == null || !session.ready)
          const Padding(
            padding: EdgeInsets.only(top: 16),
            child: Text('Connect the radio to see the mesh.'),
          )
        else if (!mesh)
          Padding(
            padding: const EdgeInsets.only(top: 16),
            child: Text(
              'Routes need mesh bridge firmware (usb-master-0.4.2 or later). '
              'This bridge reports ${session.values['Firmware'] ?? 'unknown firmware'}.',
            ),
          ),
        const SizedBox(height: 16),
        if (modules.isEmpty)
          const Text(
            'No modules yet. Run Discover modules on the Modules page.',
          )
        else ...[
          Card(
            child: Padding(
              padding: const EdgeInsets.all(8),
              child: SizedBox(
                height: math.max(260, topology.rows * 96.0),
                width: double.infinity,
                child: CustomPaint(
                  key: const ValueKey('mesh-topology'),
                  painter: MeshTopologyPainter(topology, theme.colorScheme),
                ),
              ),
            ),
          ),
          const SizedBox(height: 8),
          const _Legend(),
          const SizedBox(height: 16),
          _RouteTable(modules: modules),
        ],
      ],
    );
  }
}

class _Legend extends StatelessWidget {
  const _Legend();

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    Widget item(String text, Color color, {bool dashed = false}) => Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        CustomPaint(
          size: const Size(28, 10),
          painter: _LineSample(color, dashed),
        ),
        const SizedBox(width: 6),
        Text(text),
      ],
    );
    return Wrap(
      spacing: 20,
      runSpacing: 8,
      children: [
        item('Direct (colour = signal)', const Color(0xFF4CE65A)),
        item('Through a relay', scheme.primary),
        item('Flooded', scheme.tertiary, dashed: true),
        item('Not known yet', scheme.outline, dashed: true),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 12,
              height: 12,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xFFFF4D4D), width: 2),
              ),
            ),
            const SizedBox(width: 6),
            const Text('Last request got no reply'),
          ],
        ),
      ],
    );
  }
}

class _LineSample extends CustomPainter {
  _LineSample(this.color, this.dashed);
  final Color color;
  final bool dashed;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 3;
    final a = Offset(0, size.height / 2),
        b = Offset(size.width, size.height / 2);
    dashed ? _dashed(canvas, a, b, paint) : canvas.drawLine(a, b, paint);
  }

  @override
  bool shouldRepaint(_LineSample old) =>
      old.color != color || old.dashed != dashed;
}

void _dashed(Canvas canvas, Offset a, Offset b, Paint paint) {
  final length = (b - a).distance;
  if (length == 0) return;
  final step = (b - a) / length;
  for (var d = 0.0; d < length; d += 12) {
    canvas.drawLine(a + step * d, a + step * math.min(d + 6, length), paint);
  }
}

class MeshTopologyPainter extends CustomPainter {
  MeshTopologyPainter(this.topology, this.scheme);
  final MeshTopology topology;
  final ColorScheme scheme;

  static const radius = 24.0;

  Offset _at(MeshNode node, Size size) => Offset(
    size.width * (node.column == 1 ? 0.45 : 0.8),
    size.height * node.y,
  );

  Offset _bridge(Size size) =>
      Offset(math.min(80, size.width * 0.12), size.height / 2);

  void _text(
    Canvas canvas,
    String text,
    Offset center,
    TextStyle style, {
    double maxWidth = 160,
  }) {
    final painter = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
      textAlign: TextAlign.center,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: maxWidth);
    painter.paint(
      canvas,
      center - Offset(painter.width / 2, painter.height / 2),
    );
  }

  @override
  void paint(Canvas canvas, Size size) {
    final bridge = _bridge(size);
    final small = TextStyle(color: scheme.onSurfaceVariant, fontSize: 12);
    // Links first, under the nodes.
    for (final link in topology.links) {
      final to = topology.node(link.to);
      if (to == null) continue;
      final from = link.from == null ? null : topology.node(link.from!);
      final a = from == null ? bridge : _at(from, size);
      final b = _at(to, size);
      final color = switch (link.kind) {
        MeshLinkKind.direct => signalColor(link.rssi, scheme),
        MeshLinkKind.relayed => scheme.primary,
        MeshLinkKind.flooded => scheme.tertiary,
        MeshLinkKind.unknown => scheme.outline,
      };
      final paint = Paint()
        ..color = color
        ..strokeWidth = link.kind == MeshLinkKind.unknown ? 2 : 3;
      final dashed =
          link.kind == MeshLinkKind.flooded ||
          link.kind == MeshLinkKind.unknown;
      dashed ? _dashed(canvas, a, b, paint) : canvas.drawLine(a, b, paint);
      if (link.rssi != null && link.kind != MeshLinkKind.unknown) {
        // The RSSI of the copy the bridge heard (the relay's, when relayed).
        _text(
          canvas,
          '${link.rssi} dBm',
          Offset.lerp(a, b, 0.5)! - const Offset(0, 12),
          small,
        );
      }
    }

    // The bridge.
    final bridgeRect = Rect.fromCenter(center: bridge, width: 72, height: 44);
    canvas.drawRRect(
      RRect.fromRectAndRadius(bridgeRect, const Radius.circular(10)),
      Paint()..color = scheme.primaryContainer,
    );
    _text(
      canvas,
      'Bridge',
      bridge,
      TextStyle(color: scheme.onPrimaryContainer, fontWeight: FontWeight.w600),
    );

    for (final node in topology.nodes) {
      final center = _at(node, size);
      final failed = node.failed;
      canvas.drawCircle(
        center,
        radius,
        Paint()..color = scheme.surfaceContainerHighest,
      );
      canvas.drawCircle(
        center,
        radius,
        Paint()
          ..style = PaintingStyle.stroke
          ..strokeWidth = failed ? 3 : 2
          ..color = failed
              ? const Color(0xFFFF4D4D)
              : node.module == null
              ? scheme.outline
              : scheme.primary,
      );
      _text(
        canvas,
        node.module == null ? '?' : node.id.substring(4),
        center,
        TextStyle(
          color: scheme.onSurface,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
        maxWidth: radius * 2,
      );
      final caption = node.module == null
          ? '0x${node.id} (not discovered)'
          : '0x${node.id}';
      _text(canvas, caption, center + const Offset(0, radius + 12), small);
    }
  }

  @override
  bool shouldRepaint(MeshTopologyPainter old) => true;
}

class _RouteTable extends StatelessWidget {
  const _RouteTable({required this.modules});
  final List<FieldModule> modules;

  String _ago(DateTime? time) {
    if (time == null) return '—';
    final seconds = DateTime.now().difference(time).inSeconds;
    if (seconds < 60) return '${seconds}s ago';
    if (seconds < 3600) return '${seconds ~/ 60}m ago';
    return '${seconds ~/ 3600}h ago';
  }

  @override
  Widget build(BuildContext context) {
    final sorted = modules.toList()..sort((a, b) => a.uid.compareTo(b.uid));
    int count(FieldModule module, ModuleRoute route) =>
        module.routeCounts[route] ?? 0;
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(
        key: const ValueKey('mesh-routes'),
        columns: const [
          DataColumn(label: Text('Module')),
          DataColumn(label: Text('RSSI'), numeric: true),
          DataColumn(label: Text('Hops'), numeric: true),
          DataColumn(label: Text('Last route')),
          DataColumn(label: Text('Attempts'), numeric: true),
          DataColumn(label: Text('Direct'), numeric: true),
          DataColumn(label: Text('Via relay'), numeric: true),
          DataColumn(label: Text('Flooded'), numeric: true),
          DataColumn(label: Text('Failed'), numeric: true),
          DataColumn(label: Text('Last reply')),
        ],
        rows: [
          for (final module in sorted)
            DataRow(
              cells: [
                DataCell(Text(module.id)),
                DataCell(
                  Text(module.rssi == null ? '—' : '${module.rssi} dBm'),
                ),
                DataCell(Text(module.hops?.toString() ?? '—')),
                DataCell(
                  Text(
                    module.lastDeliveryFailed
                        ? 'No reply'
                        : (module.routeText ?? '—'),
                    style: module.lastDeliveryFailed
                        ? const TextStyle(color: Color(0xFFFF4D4D))
                        : null,
                  ),
                ),
                DataCell(Text(module.attempts?.toString() ?? '—')),
                DataCell(Text('${count(module, ModuleRoute.direct)}')),
                DataCell(Text('${count(module, ModuleRoute.relayed)}')),
                DataCell(Text('${count(module, ModuleRoute.flooded)}')),
                DataCell(Text('${module.failedDeliveries}')),
                DataCell(Text(_ago(module.lastSeen))),
              ],
            ),
        ],
      ),
    );
  }
}
