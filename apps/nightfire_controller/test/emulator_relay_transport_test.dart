import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:nightfire_controller/transport/emulator_relay_transport.dart';

void main() {
  test(
    'Concurrent heartbeat and query writes preserve every frame in order',
    () async {
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final transport = EmulatorRelayTransport(
        host: '127.0.0.1',
        port: server.port,
      );
      final accepted = server.first;
      await transport.connect('emulator-relay');
      final peer = await accepted;
      final received = <int>[];
      final complete = Completer<void>();
      final subscription = peer.listen((bytes) {
        received.addAll(bytes);
        if (received.length == 400) complete.complete();
      });
      await Future.wait([
        for (var index = 0; index < 100; index++)
          transport.write(Uint8List.fromList([0xaa, index, 0x18, 0x55])),
      ]);
      await complete.future.timeout(const Duration(seconds: 3));
      expect(received, [
        for (var index = 0; index < 100; index++) ...[0xaa, index, 0x18, 0x55],
      ]);
      await subscription.cancel();
      await transport.dispose();
      peer.destroy();
      await server.close();
    },
  );

  test(
    'Local relay carries the exact bridge bytes in both directions',
    () async {
      final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final transport = EmulatorRelayTransport(
        host: '127.0.0.1',
        port: server.port,
      );
      final accepted = server.first;
      final reply = transport.incoming.first;
      await transport.connect('emulator-relay');
      final peer = await accepted;
      final request = peer.first;
      await transport.write(Uint8List.fromList([1, 2, 3, 255]));
      expect(await request, [1, 2, 3, 255]);
      peer.add([4, 5, 6]);
      await peer.flush();
      expect(await reply, [4, 5, 6]);
      await transport.dispose();
      peer.destroy();
      await server.close();
    },
  );
  test('A closed relay reports connection loss', () async {
    final server = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
    final transport = EmulatorRelayTransport(
      host: '127.0.0.1',
      port: server.port,
    );
    final accepted = server.first;
    final error = Completer<Object>();
    final subscription = transport.incoming.listen(
      (_) {},
      onError: error.complete,
    );
    await transport.connect('emulator-relay');
    (await accepted).destroy();
    expect(
      await error.future.timeout(const Duration(seconds: 2)),
      isStateError,
    );
    await subscription.cancel();
    await transport.dispose();
    await server.close();
  });
}
