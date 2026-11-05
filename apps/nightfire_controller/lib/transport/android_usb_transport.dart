import 'dart:async';

import 'package:flutter/services.dart';

import 'radio_transport.dart';

class AndroidUsbTransport implements RadioTransport {
  static const _methods = MethodChannel('com.nightfire/usb');
  static const _events = EventChannel('com.nightfire/usb_rx');
  late final Stream<Uint8List> _incoming = _events
      .receiveBroadcastStream()
      .cast<Uint8List>();

  @override
  Stream<Uint8List> get incoming => _incoming;

  @override
  Future<List<RadioDevice>> devices() async {
    final devices = await _methods.invokeListMethod<dynamic>('devices') ?? [];
    return devices
        .map(
          (device) =>
              RadioDevice(device['id'] as String, device['label'] as String),
        )
        .toList();
  }

  @override
  Future<void> connect(String id) => _methods.invokeMethod<void>('connect', id);

  @override
  Future<void> write(Uint8List bytes) =>
      _methods.invokeMethod<void>('write', bytes);

  @override
  Future<void> disconnect() => _methods.invokeMethod<void>('disconnect');

  @override
  Future<void> dispose() => disconnect();
}
