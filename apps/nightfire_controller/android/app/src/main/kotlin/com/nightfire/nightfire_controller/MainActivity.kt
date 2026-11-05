package com.nightfire.nightfire_controller

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import android.hardware.usb.UsbRequest
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.EventChannel
import io.flutter.plugin.common.MethodChannel
import java.nio.ByteBuffer
import java.util.concurrent.Executors
import java.util.concurrent.TimeoutException

class MainActivity : FlutterActivity() {
    private val handler = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var usb: UsbManager
    private val permissionAction = "com.nightfire.nightfire_controller.USB_PERMISSION"
    private var sink: EventChannel.EventSink? = null
    private var pending: MethodChannel.Result? = null
    private var pendingDevice: UsbDevice? = null
    @Volatile private var connection: UsbDeviceConnection? = null
    private var output: UsbEndpoint? = null
    private var currentDevice: UsbDevice? = null
    private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == permissionAction) {
                val result = pending ?: return
                val device = pendingDevice
                pending = null
                pendingDevice = null
                if (device != null && usb.hasPermission(device)) {
                    open(device, result)
                } else {
                    result.error("permission", "USB permission denied", null)
                }
            } else if (intent.action == UsbManager.ACTION_USB_DEVICE_DETACHED) {
                @Suppress("DEPRECATION")
                val device = intent.getParcelableExtra<UsbDevice>(UsbManager.EXTRA_DEVICE)
                if (device?.deviceId == currentDevice?.deviceId) {
                    close()
                    sink?.error("detached", "USB cable removed", null)
                }
            }
        }
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        usb = getSystemService(Context.USB_SERVICE) as UsbManager
        val filter = IntentFilter(permissionAction).apply {
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        else registerReceiver(receiver, filter)
        registered = true
        EventChannel(flutterEngine.dartExecutor.binaryMessenger, "com.nightfire/usb_rx")
            .setStreamHandler(object : EventChannel.StreamHandler {
                override fun onListen(arguments: Any?, events: EventChannel.EventSink) { sink = events }
                override fun onCancel(arguments: Any?) { sink = null }
            })
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "com.nightfire/usb")
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "devices" -> result.success(usb.deviceList.values.filter {
                        it.vendorId == 0x239a && it.productId == 0x00b3
                    }.map { mapOf("id" to it.deviceName, "label" to "${it.productName ?: "Nightfire radio"} (${it.deviceName})") })
                    "connect" -> {
                        if (pending != null) result.error("busy", "USB permission request pending", null)
                        else {
                            // A replug renumbers the bus path; fall back to the
                            // radio's VID/PID when exactly one radio is attached.
                            val radios = usb.deviceList.values.filter {
                                it.vendorId == 0x239a && it.productId == 0x00b3
                            }
                            val device = usb.deviceList[call.arguments as? String]
                                ?: radios.singleOrNull()
                            if (device == null || device.vendorId != 0x239a || device.productId != 0x00b3) {
                                result.error("device", "Nightfire radio not found", null)
                            } else if (usb.hasPermission(device)) open(device, result)
                            else {
                                pending = result
                                pendingDevice = device
                                val request = PendingIntent.getBroadcast(this, 0,
                                    Intent(permissionAction).setPackage(packageName), PendingIntent.FLAG_IMMUTABLE)
                                usb.requestPermission(device, request)
                            }
                        }
                    }
                    "disconnect" -> {
                        pending?.error("cancelled", "Connection cancelled", null)
                        pending = null
                        pendingDevice = null
                        close()
                        result.success(null)
                    }
                    "write" -> {
                        val data = call.arguments as? ByteArray
                        val active = connection
                        val endpoint = output
                        if (data == null || active == null || endpoint == null) result.error("closed", "USB disconnected", null)
                        else worker.execute {
                            try {
                                var offset = 0
                                while (offset < data.size) {
                                    val written = active.bulkTransfer(endpoint, data, offset, data.size - offset, 1000)
                                    if (written <= 0) error("USB write timed out")
                                    offset += written
                                }
                                handler.post { result.success(null) }
                            } catch (error: Exception) {
                                handler.post { result.error("write", error.message, null) }
                            }
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun open(device: UsbDevice, result: MethodChannel.Result) {
        close()
        var dataInterface: UsbInterface? = null
        var inputEndpoint: UsbEndpoint? = null
        var outputEndpoint: UsbEndpoint? = null
        var controlInterface: UsbInterface? = null
        for (index in 0 until device.interfaceCount) {
            val candidate = device.getInterface(index)
            if (candidate.interfaceClass == UsbConstants.USB_CLASS_COMM) controlInterface = candidate
            if (candidate.interfaceClass != UsbConstants.USB_CLASS_CDC_DATA) continue
            for (endpointIndex in 0 until candidate.endpointCount) {
                val endpoint = candidate.getEndpoint(endpointIndex)
                if (endpoint.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
                if (endpoint.direction == UsbConstants.USB_DIR_IN) inputEndpoint = endpoint else outputEndpoint = endpoint
            }
            dataInterface = candidate
            break
        }
        val control = controlInterface
        val data = dataInterface
        val input = inputEndpoint
        val outbound = outputEndpoint
        if (control == null || data == null || input == null || outbound == null) {
            result.error("cdc", "Radio has no supported USB CDC interfaces", null)
            return
        }
        val active = usb.openDevice(device)
        if (active == null) { result.error("open", "Unable to open USB radio", null); return }
        try {
            check(active.claimInterface(control, true)) { "Cannot claim CDC control interface" }
            check(active.claimInterface(data, true)) { "Cannot claim CDC data interface" }
            val coding = byteArrayOf(0x00, 0xc2.toByte(), 0x01, 0x00, 0x00, 0x00, 0x08)
            check(active.controlTransfer(0x21, 0x20, 0, control.id, coding, 7, 1000) == 7) { "CDC line coding rejected" }
            check(active.controlTransfer(0x21, 0x22, 3, control.id, null, 0, 1000) >= 0) { "CDC DTR rejected" }
            currentDevice = device
            output = outbound
            connection = active
            Thread({
                try {
                    if (Build.VERSION.SDK_INT >= 26) readQueued(active, input)
                    else readPolling(active, input)
                } catch (error: Exception) {
                    handler.post {
                        if (connection === active) {
                            close()
                            sink?.error("read", error.message, null)
                        }
                    }
                }
            }, "nightfire-usb-reader").start()
            result.success(null)
        } catch (error: Exception) {
            active.close()
            result.error("open", error.message, null)
        }
    }

    private fun deliver(active: UsbDeviceConnection, packet: ByteArray) {
        handler.post { if (connection === active) sink?.success(packet) }
    }

    /**
     * Keeps one IN request queued at all times. A short bulkTransfer timeout
     * cancels the transfer, and some host controllers (seen on a Galaxy Tab
     * A9+) drop a reply that arrives during the cancel.
     */
    @android.annotation.TargetApi(26)
    private fun readQueued(active: UsbDeviceConnection, input: UsbEndpoint) {
        val request = UsbRequest()
        check(request.initialize(active, input)) { "Cannot start USB reads" }
        val buffer = ByteBuffer.allocate(1024)
        try {
            var queued = false
            while (connection === active) {
                if (!queued) {
                    buffer.clear()
                    check(request.queue(buffer)) { "USB read request rejected" }
                    queued = true
                }
                val done = try {
                    active.requestWait(250)
                } catch (_: TimeoutException) {
                    continue // Still queued: no data is discarded.
                } ?: break
                if (done !== request) continue
                queued = false
                val count = buffer.position()
                if (count > 0) {
                    val packet = ByteArray(count)
                    buffer.flip()
                    buffer.get(packet)
                    deliver(active, packet)
                }
            }
        } finally {
            // The connection may already be closed by disconnect().
            runCatching { request.cancel() }
            runCatching { request.close() }
        }
    }

    private fun readPolling(active: UsbDeviceConnection, input: UsbEndpoint) {
        val buffer = ByteArray(1024)
        while (connection === active) {
            val count = active.bulkTransfer(input, buffer, buffer.size, 200)
            if (count > 0) deliver(active, buffer.copyOf(count))
        }
    }

    private fun close() {
        val active = connection
        connection = null
        output = null
        currentDevice = null
        active?.close()
    }

    override fun onDestroy() {
        pending?.error("closed", "Activity closed", null)
        pending = null
        close()
        if (registered) unregisterReceiver(receiver)
        worker.shutdownNow()
        super.onDestroy()
    }
}
