import time
import nrf_radio
import bridge_radio
from usb_bridge_transport import UsbHexTransport
from usb_monitor_nfcp import decode_query, radio_reply, MasterKeepalive
from command_interface import CommandInterface

try:
    import nrf_bridge_config as config
except ImportError:
    config = None

network = getattr(config, "NETWORK_ID", 0x04D2)
channel = getattr(config, "RADIO_CHANNEL", 90)
address = getattr(config, "RADIO_ADDRESS", 0x67676767)
power = getattr(config, "RADIO_TX_POWER", 8)
transport = UsbHexTransport()
parser = bridge_radio.BridgeFrameParser()
radio_ready = False
interface = CommandInterface(config, led_write=getattr(nrf_radio, "write_ws2811", None))
interface.keepalive_supported = True
interface.group_settings_supported = True
keepalive = MasterKeepalive(network, nrf_radio.send, time.ticks_ms, time.ticks_diff)
master = None
maintenance = None
if getattr(config, 'MASTER_ENABLED', False):
    from usb_master_control import MasterControl
    from usb_master_maintenance import BridgeMaintenance
    master = MasterControl(nrf_radio.send, time.ticks_ms, time.ticks_diff)
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
            for value in transport.read(64):
                frame = parser.feed(value)
                if not frame:
                    continue
                command = frame.cmd
                flags = bridge_radio.FLAG_RESPONSE
                if command == bridge_radio.CMD_PING:
                    command = bridge_radio.CMD_PONG
                    payload = b""
                elif command == bridge_radio.CMD_GET_INFO:
                    version = b"usb-master-0.4.0" if master else b"usb-monitor-0.3.2"
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
                    payload = b""
                elif command == bridge_radio.CMD_GET_KEEPALIVE_STATUS and not frame.payload:
                    payload = keepalive.payload()
                elif command == bridge_radio.CMD_SEND_NFCP and (
                    master.permit(frame.payload, interface.session_state() == 1,
                                  interface.switch if interface.input is not None else None)
                    if master else decode_query(frame.payload, interface.session_state() == 1)
                ):
                    sequence = frame.seq if frame.seq != 255 else 0
                    packet = bridge_radio.int_to_be(network, 2) + bytes([sequence]) + frame.payload
                    if nrf_radio.send(packet):
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
                transport.write(bridge_radio.build_frame(frame.seq, command, payload, flags))
        if master:
            master.network = network
            master.poll(interface.session_state() == 1, radio_ready,
                        interface.switch if interface.input is not None else None)
            if interface.session_state() != 1:
                maintenance.abort()
        keepalive.poll(interface.session_state() == 1 and not (master and master.needs_rearm), radio_ready)
        interface.faults = (interface.faults & ~16) | (16 if keepalive.failed else 0)
        packet = nrf_radio.recv(50)
        if packet:
            reply = radio_reply(packet, network)
            if reply:
                if master:
                    master.observe_reply(reply)
                interface.record_rx()
                rssi = nrf_radio.last_rssi() & 65535
                payload = bridge_radio.int_to_be(rssi, 2) + b"\x00" + reply
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
