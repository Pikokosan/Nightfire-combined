import time
import nrf_radio
import bridge_radio
import mesh
from usb_bridge_transport import UsbHexTransport
from usb_monitor_nfcp import decode_query, radio_reply, MasterKeepalive
from command_interface import CommandInterface

try:
    import nrf_bridge_config as config
except ImportError:
    config = None

network = getattr(config, "NETWORK_ID", 0x04D2)
channel = getattr(config, "RADIO_CHANNEL", 90)
# Mesh radio (docs/mesh_radio_design.md). RADIO_ADDRESS in old configs is
# the pre-mesh address (0x67676767), so it is ignored.
address = getattr(config, "MESH_RADIO_ADDRESS", mesh.MESH_RADIO_ADDRESS)
power = getattr(config, "RADIO_TX_POWER", 8)
transport = UsbHexTransport()
# Everything this bridge sends goes out as a mesh frame with repeats. The
# keepalive and master stop/disarm code still build pre-mesh packets, so
# they send through send_legacy, which converts them.
bridge_mesh = mesh.MeshBridge(nrf_radio.send, nrf_radio.device_id32())
# Bench test only: nodes this bridge pretends it can't hear directly.
bridge_mesh.deaf_to = tuple(getattr(config, "MESH_BENCH_DEAF_TO", ()) or ())
if bridge_mesh.deaf_to:
    print("MESH BENCH: deaf to", " ".join("%08X" % u for u in bridge_mesh.deaf_to))
RADIO_POLL_MS = 20   # was 50; bounds how long an app command waits
parser = bridge_radio.BridgeFrameParser()
radio_ready = False
interface = CommandInterface(config, led_write=getattr(nrf_radio, "write_ws2811", None))
interface.keepalive_supported = True
interface.group_settings_supported = True
keepalive = MasterKeepalive(network, bridge_mesh.send_legacy, time.ticks_ms, time.ticks_diff)
master = None
maintenance = None
trial_confirmed = False
if getattr(config, 'MASTER_ENABLED', False):
    from usb_master_control import MasterControl
    from usb_master_maintenance import BridgeMaintenance
    master = MasterControl(bridge_mesh.send_legacy, time.ticks_ms, time.ticks_diff)
    maintenance = BridgeMaintenance(nrf_radio, config)


def stats_payload():
    stats = nrf_radio.stats()
    payload = bytearray()
    for name in ("tx_ok", "tx_timeout", "rx_ok", "rx_timeout", "rx_crc_error", "rx_bad_len"):
        payload.extend(bridge_radio.int_to_be(stats.get(name, 0), 4))
    payload.extend(bytes([stats.get("channel", channel), stats.get("tx_power", power) & 255]))
    payload.extend(bridge_radio.int_to_be(stats.get("last_rssi", 0) & 65535, 2))
    payload.extend(bridge_radio.int_to_be(stats.get("max_payload", 252), 2))
    payload.extend(bridge_radio.int_to_be(stats.get("address", address), 4))
    return payload


while True:
    try:
        if not radio_ready:
            nrf_radio.init(channel=channel, tx_power=power, address=address)
            radio_ready = True
        if transport.any():
            host_rx_us = time.ticks_us()   # time code ages from here
            for frame in parser.feed_bytes(transport.read(512)):
                command = frame.cmd
                flags = bridge_radio.FLAG_RESPONSE
                if command == bridge_radio.CMD_PING:
                    command = bridge_radio.CMD_PONG
                    payload = b""
                elif command == bridge_radio.CMD_GET_INFO:
                    version = b"usb-master-0.4.2" if master else b"usb-monitor-0.3.3"   # mesh: reliable delivery
                    payload = (bridge_radio.int_to_be(nrf_radio.device_id32(), 4) +
                               b"\x00\x00" + bridge_radio.int_to_be(network, 2) +
                               bytes([len(version)]) + version)
                elif command == bridge_radio.CMD_GET_STATS:
                    payload = stats_payload()
                elif command == bridge_radio.CMD_GET_INTERFACE_STATUS and not frame.payload:
                    interface.poll(radio_ready)
                    payload = interface.payload()
                elif command == bridge_radio.CMD_HOST_HEARTBEAT and len(frame.payload) == 4:
                    if master and interface.session_id is not None and interface.session_id != bytes(frame.payload):
                        master.stop()
                    interface.heartbeat(frame.payload)
                    if maintenance and not trial_confirmed:
                        # A working app session proves this firmware can be
                        # controlled over USB, so keep it (see main.py entry).
                        maintenance.confirm_trial()
                        trial_confirmed = True
                    payload = b""
                elif command == bridge_radio.CMD_GET_KEEPALIVE_STATUS and not frame.payload:
                    payload = keepalive.payload()
                elif command == bridge_radio.CMD_SEND_NFCP_RELIABLE and len(frame.payload) > 6 and (
                    master.permit(frame.payload[6:], interface.session_state() == 1,
                                  interface.switch if interface.input is not None else None)
                    if master else decode_query(frame.payload[6:], interface.session_state() == 1)
                ):
                    # Answered from bridge_mesh.poll() when the delivery ends.
                    bridge_mesh.send_reliable(
                        network, bridge_radio.int_from_be(frame.payload[0:4]), bytes(frame.payload[6:]),
                        bridge_radio.int_from_be(frame.payload[4:6]), frame.seq, host_rx_us)
                    if master:
                        master.sent(frame.payload[6:])
                    payload = None
                elif command == bridge_radio.CMD_SEND_NFCP and (
                    master.permit(frame.payload, interface.session_state() == 1,
                                  interface.switch if interface.input is not None else None)
                    if master else decode_query(frame.payload, interface.session_state() == 1)
                ):
                    if bridge_mesh.send_frame(network, bytes(frame.payload), host_rx_us):
                        payload = b"\x00"
                        if master:
                            master.sent(frame.payload)
                    else:
                        flags |= bridge_radio.FLAG_ERROR
                        payload = b"\x04"
                elif (master and interface.session_state() == 1 and
                      not master.may_be_armed and not master.stop_pending and
                      command in (0x12, 0x13, 0x14, 0x16, 0x31, 0x32, 0x33, 0x34)):
                    try:
                        payload = b''
                        if command == 0x12 and len(frame.payload) == 2:
                            new_network = bridge_radio.int_from_be(frame.payload)
                            maintenance.save(new_network, channel)
                            network = new_network
                            keepalive.network = network
                        elif command == 0x13 and len(frame.payload) == 1 and (
                                frame.payload[0] == 0 or 74 <= frame.payload[0] <= 100):
                            new_channel = frame.payload[0]
                            nrf_radio.channel(new_channel)
                            bridge_mesh.queue.clear()
                            maintenance.save(network, new_channel)
                            channel = new_channel
                        elif command == 0x14 and not frame.payload:
                            payload = maintenance.scan(channel)
                        elif command == 0x16 and not frame.payload:
                            nrf_radio.clear_stats()
                        elif command in (0x32, 0x33, 0x34):
                            maintenance.file_command(command, frame.payload)
                        elif command == 0x31 and not frame.payload:
                            transport.write(bridge_radio.build_frame(frame.seq, command, b'', flags))
                            time.sleep_ms(150)
                            import machine
                            machine.reset()
                        else:
                            raise ValueError('Invalid maintenance request')
                    except Exception as error:
                        flags |= bridge_radio.FLAG_ERROR
                        payload = b'\x04'
                        print('Master maintenance error:', error)
                else:
                    interface.reject()
                    flags |= bridge_radio.FLAG_ERROR
                    payload = b"\x05"
                if payload is not None:
                    transport.write(bridge_radio.build_frame(frame.seq, command, payload, flags))
        for tag, result in bridge_mesh.poll():
            transport.write(bridge_radio.build_frame(
                tag, bridge_radio.CMD_SEND_NFCP_RELIABLE, result, bridge_radio.FLAG_RESPONSE))
        if master:
            master.network = network
            master.poll(interface.session_state() == 1, radio_ready,
                        interface.switch if interface.input is not None else None)
            if interface.session_state() != 1:
                maintenance.abort()
        keepalive.poll(interface.session_state() == 1 and not (master and master.needs_rearm), radio_ready)
        interface.faults = (interface.faults & ~16) | (16 if keepalive.failed else 0)
        interface.module_arm = master.arm_state() if master else None
        interface.keepalive_at = keepalive.last_success
        bridge_mesh.service()
        packet = nrf_radio.recv(bridge_mesh.wait_ms(RADIO_POLL_MS))
        if packet:
            # Drops duplicates, relayed copies of our own frames and pre-mesh
            # payloads before any further work.
            rssi = nrf_radio.last_rssi()
            frame_bytes = bridge_mesh.receive(packet, network, rssi)
            reply = radio_reply(bridge_radio.int_to_be(network, 2) + frame_bytes, network) if frame_bytes else None
            if reply:
                if master:
                    master.observe_reply(reply)
                interface.record_rx()
                # rssi(2) hops(1) frame: the third byte was always 0 before.
                payload = bridge_radio.int_to_be(rssi & 65535, 2) + bytes((bridge_mesh.last_hops,)) + reply
                transport.write(bridge_radio.build_frame(0, bridge_radio.CMD_NFCP_RX, payload))
        interface.poll(radio_ready)
        time.sleep_ms(1)
    except KeyboardInterrupt:
        raise
    except Exception as error:
        if master:
            master.stop()
        print("USB monitor error:", error)
        radio_ready = False
        time.sleep_ms(250)
