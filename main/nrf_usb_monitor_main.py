# USB radio bridge for the Android app (installed as main.py, or bundled into
# usb_master_bundle.mpy by scripts/build/build_usb_master_bundle.py).
#
# The app sends bridge frames (bridge_radio.py) over USB (usb_bridge_transport
# .py); the bridge answers each one, passes permitted NFCP frames to the
# modules over the mesh radio (mesh.py) and hands every module reply back to
# the app as CMD_NFCP_RX. Without MASTER_ENABLED in nrf_bridge_config.py it
# is a monitor: only discovery and read-only queries reach the modules
# (usb_monitor_nfcp.py). With it, usb_master_control.py decides what may be
# sent, and stops and disarms everything when the app or the ARM key goes
# away.
#
# The bundle puts every bridge file into one namespace, so the top-level
# names here must not be used in the other bridge files.

import time
import nrf_radio
import bridge_radio
import mesh
from usb_bridge_transport import UsbHexTransport
from usb_monitor_nfcp import decode_query, radio_reply, MasterKeepalive
from command_interface import CommandInterface
from nfcp_frame import NFCP_PAIR_NETWORK_ID, NFCP_PAIR_RADIO_CHANNEL, nfcp_valid_channel

try:
    import nrf_bridge_config as config
except ImportError:
    config = None

MASTER_VERSION = b"usb-master-0.4.7"
MONITOR_VERSION = b"usb-monitor-0.3.4"
RADIO_POLL_MS = 20   # was 50; bounds how long an app command waits

# Replies with FLAG_ERROR carry one of these.
BRIDGE_FAILED = b"\x04"     # the bridge tried and it failed
BRIDGE_REFUSED = b"\x05"    # not allowed now, or not a valid request
# A command handler's answer when the request is refused.
BRIDGE_REFUSE = object()

# Maintenance commands: master firmware only, with an app session and
# nothing this bridge may have armed.
BRIDGE_MAINTENANCE = (bridge_radio.CMD_SET_NETWORK, bridge_radio.CMD_SET_CHANNEL,
                      bridge_radio.CMD_SCAN_CHANNELS, bridge_radio.CMD_CLEAR_STATS,
                      bridge_radio.CMD_RESET, bridge_radio.CMD_FILE_BEGIN,
                      bridge_radio.CMD_FILE_WRITE, bridge_radio.CMD_FILE_END)
BRIDGE_FILE_COMMANDS = (bridge_radio.CMD_FILE_BEGIN, bridge_radio.CMD_FILE_WRITE,
                        bridge_radio.CMD_FILE_END)


class UsbBridge:
    def __init__(self):
        self.network = getattr(config, "NETWORK_ID", 0x04D2)
        self.channel = getattr(config, "RADIO_CHANNEL", 90)
        # Mesh radio (docs/mesh_radio_design.md). RADIO_ADDRESS in old configs
        # is the pre-mesh address (0x67676767), so it is ignored.
        self.address = getattr(config, "MESH_RADIO_ADDRESS", mesh.MESH_RADIO_ADDRESS)
        self.power = getattr(config, "RADIO_TX_POWER", 8)
        self.transport = UsbHexTransport()
        # Everything this bridge sends goes out as a mesh frame with repeats.
        # The keepalive and master stop/disarm code still build pre-mesh
        # packets, so they send through send_legacy, which converts them.
        self.mesh = mesh.MeshBridge(nrf_radio.send, nrf_radio.device_id32())
        # Bench test only: nodes this bridge pretends it can't hear directly.
        self.mesh.deaf_to = tuple(getattr(config, "MESH_BENCH_DEAF_TO", ()) or ())
        if self.mesh.deaf_to:
            print("MESH BENCH: deaf to", " ".join("%08X" % u for u in self.mesh.deaf_to))
        self.parser = bridge_radio.BridgeFrameParser()
        self.radio_ready = False
        interface = self.interface = CommandInterface(
            config, led_write=getattr(nrf_radio, "write_ws2811", None))
        interface.keepalive_supported = True
        interface.group_settings_supported = True
        master_enabled = bool(getattr(config, "MASTER_ENABLED", False))
        # Capability 128: the master policy relays CMD_FIRE_PULSE (cue hold time).
        interface.fire_pulse_supported = master_enabled
        self.keepalive = MasterKeepalive(self.network, self.mesh.send_legacy,
                                         time.ticks_ms, time.ticks_diff)
        self.master = None
        self.maintenance = None
        self.pairing = None
        self.trial_confirmed = False
        if master_enabled:
            from usb_master_control import MasterControl, BridgePairing
            from usb_master_maintenance import BridgeMaintenance
            self.master = MasterControl(self.mesh.send_legacy, time.ticks_ms, time.ticks_diff)
            self.maintenance = BridgeMaintenance(nrf_radio, config)
            self.pairing = BridgePairing(time.ticks_ms, time.ticks_diff)
            # Capability 4: CMD_PAIR pairs field modules with this bridge.
            interface.pairing_supported = True
        cmd = bridge_radio
        self.handlers = {
            cmd.CMD_PING: self.on_ping,
            cmd.CMD_GET_INFO: self.on_get_info,
            cmd.CMD_GET_STATS: self.on_get_stats,
            cmd.CMD_GET_INTERFACE_STATUS: self.on_interface_status,
            cmd.CMD_HOST_HEARTBEAT: self.on_heartbeat,
            cmd.CMD_GET_KEEPALIVE_STATUS: self.on_keepalive_status,
            cmd.CMD_LED_BRIGHTNESS: self.on_led_brightness,
            cmd.CMD_PAIR: self.on_pair,
            cmd.CMD_SEND_NFCP: self.on_send_nfcp,
            cmd.CMD_SEND_NFCP_RELIABLE: self.on_send_reliable,
        }
        for command in BRIDGE_MAINTENANCE:
            self.handlers[command] = self.on_maintenance
        self.flags = 0

    # --- state ---------------------------------------------------------------
    def session_active(self):
        return self.interface.session_state() == 1

    def may_change(self):
        """Settings may change: the app holds a session and nothing this
        bridge sent may have armed a module."""
        master = self.master
        return self.session_active() and not (master and (master.may_be_armed or master.stop_pending))

    def pairing_active(self):
        return self.pairing is not None and self.pairing.active()

    def end_pairing(self):
        """Back to this bridge's own channel after a pairing window."""
        self.pairing.stop()
        nrf_radio.channel(self.channel)
        self.mesh.queue.clear()

    def permitted(self, frame_bytes):
        """True if an NFCP frame from the app may go to the modules."""
        if self.master:
            return self.master.permit(frame_bytes, self.session_active(), self.interface.arm_switch())
        return decode_query(frame_bytes, self.session_active())

    def stats_payload(self):
        stats = nrf_radio.stats()
        payload = bytearray()
        for name in ("tx_ok", "tx_timeout", "rx_ok", "rx_timeout", "rx_crc_error", "rx_bad_len"):
            payload.extend(bridge_radio.int_to_be(stats.get(name, 0), 4))
        payload.extend(bytes([stats.get("channel", self.channel), stats.get("tx_power", self.power) & 255]))
        payload.extend(bridge_radio.int_to_be(stats.get("last_rssi", 0) & 65535, 2))
        payload.extend(bridge_radio.int_to_be(stats.get("max_payload", 252), 2))
        payload.extend(bridge_radio.int_to_be(stats.get("address", self.address), 4))
        return payload

    # --- USB commands ----------------------------------------------------------
    # Each handler gets the frame and when it arrived, and returns the reply
    # payload, None to answer later, or BRIDGE_REFUSE.
    def host_frame(self, frame, rx_us):
        self.flags = bridge_radio.FLAG_RESPONSE
        handler = self.handlers.get(frame.cmd)
        payload = handler(frame, rx_us) if handler else BRIDGE_REFUSE
        if payload is BRIDGE_REFUSE:
            self.interface.reject()
            self.flags |= bridge_radio.FLAG_ERROR
            payload = BRIDGE_REFUSED
        if payload is not None:
            command = bridge_radio.CMD_PONG if frame.cmd == bridge_radio.CMD_PING else frame.cmd
            self.transport.write(bridge_radio.build_frame(frame.seq, command, payload, self.flags))

    def on_ping(self, frame, rx_us):
        return b""

    def on_get_info(self, frame, rx_us):
        version = MASTER_VERSION if self.master else MONITOR_VERSION
        return (bridge_radio.int_to_be(nrf_radio.device_id32(), 4) +
                b"\x00\x00" + bridge_radio.int_to_be(self.network, 2) +
                bytes([len(version)]) + version)

    def on_get_stats(self, frame, rx_us):
        return self.stats_payload()

    def on_interface_status(self, frame, rx_us):
        if frame.payload:
            return BRIDGE_REFUSE
        self.interface.poll(self.radio_ready)
        return self.interface.payload()

    def on_heartbeat(self, frame, rx_us):
        if len(frame.payload) != 4:
            return BRIDGE_REFUSE
        interface = self.interface
        if self.master and interface.session_id is not None and interface.session_id != bytes(frame.payload):
            self.master.stop()      # another app session took over
        interface.heartbeat(frame.payload)
        if self.maintenance and not self.trial_confirmed:
            # A working app session proves this firmware can be controlled
            # over USB, so keep it (see the bundle's main.py).
            self.maintenance.confirm_trial()
            self.trial_confirmed = True
        return b""

    def on_keepalive_status(self, frame, rx_us):
        if frame.payload:
            return BRIDGE_REFUSE
        return self.keepalive.payload()

    def on_led_brightness(self, frame, rx_us):
        """Empty: read the brightness. One byte (1-255): set and save it."""
        interface = self.interface
        if not frame.payload:
            return bytes([interface.brightness])
        if not (len(frame.payload) == 1 and self.may_change() and
                interface.set_brightness(frame.payload[0])):
            return BRIDGE_REFUSE
        if self.maintenance:
            try:
                config.STATUS_LED_BRIGHTNESS = interface.brightness
                self.maintenance.save(self.network, self.channel)
            except Exception as error:
                self.flags |= bridge_radio.FLAG_ERROR
                print('LED brightness save error:', error)
        return bytes([interface.brightness])

    def on_pair(self, frame, rx_us):
        """Empty: seconds left. 0: end the window. 1-30: open it."""
        pairing = self.pairing
        if pairing is None:
            return BRIDGE_REFUSE
        if not frame.payload:
            return bytes([pairing.seconds_left()])
        if len(frame.payload) != 1:
            return BRIDGE_REFUSE
        seconds = frame.payload[0]
        if seconds == 0:
            if pairing.active():
                self.end_pairing()
            return b"\x00"
        if not (self.may_change() and pairing.start(seconds)):
            return BRIDGE_REFUSE
        nrf_radio.channel(NFCP_PAIR_RADIO_CHANNEL)
        self.mesh.queue.clear()
        return bytes([seconds])

    def on_send_nfcp(self, frame, rx_us):
        """Send an NFCP frame to the modules (flooded, with repeats)."""
        # Nothing reaches the field on the pairing channel.
        if self.pairing_active() or not self.permitted(frame.payload):
            return BRIDGE_REFUSE
        if not self.mesh.send_frame(self.network, bytes(frame.payload), rx_us):
            self.flags |= bridge_radio.FLAG_ERROR
            return BRIDGE_FAILED
        if self.master:
            self.master.sent(frame.payload)
        return b"\x00"

    def on_send_reliable(self, frame, rx_us):
        """uid(4) timeout_ms(2) NFCP frame: deliver it to one module. Answered
        from mesh.poll() when the delivery ends."""
        payload = frame.payload
        if self.pairing_active() or len(payload) <= 6 or not self.permitted(payload[6:]):
            return BRIDGE_REFUSE
        self.mesh.send_reliable(
            self.network, bridge_radio.int_from_be(payload[0:4]), bytes(payload[6:]),
            bridge_radio.int_from_be(payload[4:6]), frame.seq, rx_us)
        if self.master:
            self.master.sent(payload[6:])
        return None

    def on_maintenance(self, frame, rx_us):
        if not (self.master and self.may_change() and not self.pairing_active()):
            return BRIDGE_REFUSE
        command = frame.cmd
        payload = frame.payload
        try:
            if command == bridge_radio.CMD_SET_NETWORK and len(payload) == 2:
                network = bridge_radio.int_from_be(payload)
                self.maintenance.save(network, self.channel)
                self.network = self.keepalive.network = network
            elif (command == bridge_radio.CMD_SET_CHANNEL and len(payload) == 1 and
                  nfcp_valid_channel(payload[0])):
                channel = payload[0]
                nrf_radio.channel(channel)
                self.mesh.queue.clear()
                self.maintenance.save(self.network, channel)
                self.channel = channel
            elif command == bridge_radio.CMD_SCAN_CHANNELS and not payload:
                return self.maintenance.scan(self.channel)
            elif command == bridge_radio.CMD_CLEAR_STATS and not payload:
                nrf_radio.clear_stats()
            elif command in BRIDGE_FILE_COMMANDS:
                self.maintenance.file_command(command, payload)
            elif command == bridge_radio.CMD_RESET and not payload:
                self.transport.write(bridge_radio.build_frame(frame.seq, command, b'', self.flags))
                time.sleep_ms(150)
                import machine
                machine.reset()
            else:
                raise ValueError('Invalid maintenance request')
        except Exception as error:
            self.flags |= bridge_radio.FLAG_ERROR
            print('Master maintenance error:', error)
            return BRIDGE_FAILED
        return b''

    # --- main loop -------------------------------------------------------------
    def radio_packet(self, packet):
        # Drops duplicates, relayed copies of our own frames and pre-mesh
        # payloads before any further work.
        rssi = nrf_radio.last_rssi()
        rx_network = NFCP_PAIR_NETWORK_ID if self.pairing_active() else self.network
        frame_bytes = self.mesh.receive(packet, rx_network, rssi)
        if not frame_bytes:
            return
        reply = radio_reply(bridge_radio.int_to_be(rx_network, 2) + frame_bytes, rx_network)
        if not reply:
            return
        if self.master:
            self.master.observe_reply(reply)
        self.interface.record_rx()
        # rssi(2) hops(1) frame: the third byte was always 0 before.
        payload = bridge_radio.int_to_be(rssi & 65535, 2) + bytes((self.mesh.last_hops,)) + reply
        self.transport.write(bridge_radio.build_frame(0, bridge_radio.CMD_NFCP_RX, payload))

    def service_pairing(self):
        if not self.may_change():
            self.end_pairing()
            return
        offer = self.pairing.poll(self.network, self.channel)
        if offer:
            self.mesh.send_frame(NFCP_PAIR_NETWORK_ID, offer)
        elif not self.pairing.active():
            self.end_pairing()   # the window ran out

    def loop_once(self):
        interface = self.interface
        master = self.master
        if not self.radio_ready:
            nrf_radio.init(channel=self.channel, tx_power=self.power, address=self.address)
            self.radio_ready = True
        if self.transport.any():
            rx_us = time.ticks_us()   # time code ages from here
            for frame in self.parser.feed_bytes(self.transport.read(512)):
                self.host_frame(frame, rx_us)
        for tag, result in self.mesh.poll():
            self.transport.write(bridge_radio.build_frame(
                tag, bridge_radio.CMD_SEND_NFCP_RELIABLE, result, bridge_radio.FLAG_RESPONSE))
        if master:
            master.network = self.network
            master.poll(self.session_active(), self.radio_ready, interface.arm_switch())
            if not self.session_active():
                self.maintenance.abort()
        if self.pairing_active():
            self.service_pairing()
        self.keepalive.poll(self.session_active() and not (master and master.needs_rearm) and
                            not self.pairing_active(), self.radio_ready)
        interface.faults = (interface.faults & ~16) | (16 if self.keepalive.failed else 0)
        interface.module_arm = master.arm_state() if master else None
        interface.keepalive_at = self.keepalive.last_success
        self.mesh.service()
        packet = nrf_radio.recv(self.mesh.wait_ms(RADIO_POLL_MS))
        if packet:
            self.radio_packet(packet)
        interface.poll(self.radio_ready)

    def run(self):
        while True:
            try:
                self.loop_once()
                time.sleep_ms(1)
            except KeyboardInterrupt:
                raise
            except Exception as error:
                if self.master:
                    self.master.stop()
                print("USB monitor error:", error)
                self.radio_ready = False
                time.sleep_ms(250)


UsbBridge().run()
