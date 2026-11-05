import 'dart:typed_data';

class RadioDevice {
  const RadioDevice(this.id, this.label);
  final String id;
  final String label;
}

abstract interface class RadioTransport {
  Stream<Uint8List> get incoming;
  Future<List<RadioDevice>> devices();
  Future<void> connect(String id);
  Future<void> write(Uint8List bytes);
  Future<void> disconnect();
  Future<void> dispose();
}
