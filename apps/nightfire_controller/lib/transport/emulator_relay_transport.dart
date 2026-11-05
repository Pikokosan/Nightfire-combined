import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'radio_transport.dart';

class EmulatorRelayTransport implements RadioTransport {
  EmulatorRelayTransport({this.host = '10.0.2.2', this.port = 8765});
  final String host;
  final int port;
  final _incoming = StreamController<Uint8List>.broadcast();
  Socket? _socket;
  StreamSubscription<Uint8List>? _reader;
  Future<void> _writes = Future<void>.value();

  @override
  Stream<Uint8List> get incoming => _incoming.stream;
  @override
  Future<List<RadioDevice>> devices() async => [
    const RadioDevice(
      'emulator-relay',
      'PC USB radio through local emulator relay',
    ),
  ];
  @override
  Future<void> connect(String id) async {
    if (id != 'emulator-relay') throw ArgumentError('Unknown relay device');
    await disconnect();
    final socket = await Socket.connect(
      host,
      port,
      timeout: const Duration(seconds: 3),
    );
    socket.setOption(SocketOption.tcpNoDelay, true);
    _socket = socket;
    _reader = socket.listen(
      _incoming.add,
      onError: _incoming.addError,
      onDone: () =>
          _incoming.addError(StateError('Local emulator relay disconnected')),
    );
  }

  @override
  Future<void> write(Uint8List bytes) {
    final socket = _socket;
    final payload = Uint8List.fromList(bytes);
    final completion = _writes.then((_) async {
      if (socket == null || !identical(socket, _socket)) {
        throw StateError('Local emulator relay is disconnected');
      }
      // IOSink.flush temporarily binds the sink. Queue complete frames so
      // heartbeat and module requests cannot add bytes during another flush.
      socket.add(payload);
      await socket.flush();
    });
    _writes = completion.catchError((Object _) {});
    return completion;
  }

  @override
  Future<void> disconnect() async {
    await _reader?.cancel();
    _reader = null;
    _socket?.destroy();
    _socket = null;
  }

  @override
  Future<void> dispose() async {
    await disconnect();
    await _incoming.close();
  }
}
