# nfcp.py (updated + variable-length FIRE mask)

ESC = 0xFF

SOF_BROADCAST = 0x55
SOF_GROUP     = 0x47
SOF_UNIQUE    = 0x78
SOF_RESPONSE  = 0x6A

SOF2_BROADCAST = 0xFD
SOF2_GROUP     = 0xFC
SOF2_UNIQUE    = 0xFB
SOF2_RESPONSE  = 0xFA

ENCODED_SOF = {
    SOF_BROADCAST: bytes([ESC, SOF2_BROADCAST]),
    SOF_GROUP:     bytes([ESC, SOF2_GROUP]),
    SOF_UNIQUE:    bytes([ESC, SOF2_UNIQUE]),
    SOF_RESPONSE:  bytes([ESC, SOF2_RESPONSE]),
}

CRC_POLY = 0x1021

CMD_ARM          = 0x10
CMD_DISARM       = 0x11
CMD_FIRE         = 0x20
CMD_SET_UNIQUE   = 0x30
CMD_SET_GROUP    = 0x31
CMD_SET_NETWORK  = 0x32
CMD_SET_RADIO_CHANNEL = 0x33
# Pairing: a module in pairing mode listens on PAIR_NETWORK_ID /
# PAIR_RADIO_CHANNEL. The bridge broadcasts CMD_PAIR there with its own
# network (u16), channel (u8) and a nonce (u16); the module saves the network
# and channel, replies CMD_PAIR_RSP (same 5 bytes + its group, u16), and
# moves to them. usb_master_control.py keeps the same two values.
CMD_PAIR = 0x34
CMD_PAIR_RSP = 0x35
PAIR_NETWORK_ID = 0x7A1B
PAIR_RADIO_CHANNEL = 80
CMD_DISCOVER     = 0x40
CMD_DISCOVER_RSP = 0x41

CMD_ACK          = 0x50
CMD_NACK         = 0x51

CMD_INFO_REQ     = 0x52
CMD_INFO_RSP     = 0x53
CMD_KEEPALIVE    = 0x54

CMD_OTA_UPDATE_BEGIN  = 0x60
CMD_OTA_UPDATE_WRITE  = 0x61
CMD_OTA_UPDATE_END    = 0x62
CMD_OTA_UPDATE_ABORT  = 0x63
CMD_OTA_UPDATE_REBOOT = 0x64

CMD_SCRIPT_CLEAR = 0x70
CMD_SCRIPT_LOAD  = 0x71
CMD_SCRIPT_START = 0x72
CMD_SCRIPT_STOP  = 0x73
CMD_SCRIPT_HEARTBEAT = 0x74
CMD_TIME_SYNC = 0x75
CMD_SCRIPT_STEP = 0x76
SCRIPT_ENTRY_BYTES = 12

# INFO_RSP text fields, in order:
# app_name, app_version, radio_version, script_status,
# hardware_unique_id, configured_unique_id, battery_mv, battery_pack_type.

# speed test / ping
CMD_PING         = 0x7F   # Unique-only ping for latency/throughput tests
PING_REPLY_BYTE  = 0xAA   # Used when CMD_PING payload is empty

# continuity
CMD_CONTINUITY_REQ = 0x22   # Unique-only request
CMD_CONTINUITY_RSP = 0x23   # Response-only
CMD_RESISTANCE_REQ = 0x24   # Unique-only resistance scan request
CMD_RESISTANCE_RSP = 0x25   # Response-only resistance scan result
CMD_SET_RESISTANCE_CAL = 0x26  # Unique-only bank calibration update
CMD_OUTPUT_STATUS_REQ = 0x27   # Unique-only physical output state request
CMD_OUTPUT_STATUS_RSP = 0x28   # Response-only physical output state
CMD_FIRE_PULSE = 0x29          # Unique-only cue hold time: read (empty) or set (u16 ms)
CMD_FIRE_PULSE_RSP = 0x2A      # Response-only cue hold time (u16 ms)

RESISTANCE_PAYLOAD_VERSION = 0x01
RESISTANCE_UNIT_DECI_OHM = 0x01
RESISTANCE_UNIT_ADC_MV = 0x02
RESISTANCE_VALUE_INVALID = 0xFFFE
RESISTANCE_VALUE_OPEN = 0xFFFF
RESISTANCE_REQUEST_CALIBRATED = 0x00
RESISTANCE_REQUEST_RAW_ADC = 0x01
RESISTANCE_CALIBRATION_VERSION = 0x01

ACK_OK              = 0x00
NACK_REJECTED_ARMED = 0x01
NACK_BAD_LENGTH     = 0x02
NACK_INVALID_VALUE  = 0x03
NACK_INTERNAL_ERROR = 0x04
NACK_BUSY           = 0x05


# CRC16-CCITT (poly 0x1021) by table lookup: one step per byte instead of
# eight bit steps. Same results as the bit loop; on the nRF52840 the bit
# loop was most of the cost of handling a frame in MicroPython.
NFCP_CRC_TABLE = (
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
    0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294, 0x72F7, 0x62D6,
    0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE,
    0x2462, 0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485,
    0xA56A, 0xB54B, 0x8528, 0x9509, 0xE5EE, 0xF5CF, 0xC5AC, 0xD58D,
    0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695, 0x46B4,
    0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC,
    0x48C4, 0x58E5, 0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823,
    0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948, 0x9969, 0xA90A, 0xB92B,
    0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A,
    0x6CA6, 0x7C87, 0x4CE4, 0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41,
    0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B, 0x8D68, 0x9D49,
    0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70,
    0xFF9F, 0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78,
    0x9188, 0x81A9, 0xB1CA, 0xA1EB, 0xD10C, 0xC12D, 0xF14E, 0xE16F,
    0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046, 0x6067,
    0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E,
    0x02B1, 0x1290, 0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256,
    0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E, 0xE54F, 0xD52C, 0xC50D,
    0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C,
    0x26D3, 0x36F2, 0x0691, 0x16B0, 0x6657, 0x7676, 0x4615, 0x5634,
    0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9, 0xB98A, 0xA9AB,
    0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3,
    0xCB7D, 0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A,
    0x4A75, 0x5A54, 0x6A37, 0x7A16, 0x0AF1, 0x1AD0, 0x2AB3, 0x3A92,
    0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8, 0x8DC9,
    0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1,
    0xEF1F, 0xFF3E, 0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8,
    0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93, 0x3EB2, 0x0ED1, 0x1EF0,
)


def crc16_ccitt(data: bytes, crc=0xFFFF) -> int:
    table = NFCP_CRC_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFF00) ^ table[((crc >> 8) ^ b) & 0xFF]
    return crc


class NFCPError(Exception):
    pass


class NFCP:
    def __init__(self, node_id: int):
        self.node_id = node_id & 0xFF

    def _escape(self, data: bytes) -> bytes:
        out = bytearray()
        for b in data:
            if b == ESC:
                out += bytes([ESC, 0xFE])
            else:
                out.append(b)
        return bytes(out)

    def _frame(self, sof: int, payload: bytes) -> bytes:
        if len(payload) > 255:
            raise NFCPError("Payload too long (>255)")
        header = bytes([len(payload) & 0xFF]) + payload
        crc = crc16_ccitt(header).to_bytes(2, "big")
        # Escape the header and CRC (everything after SOF)
        return ENCODED_SOF[sof] + self._escape(header) + self._escape(crc)

    # -------------------------
    # FIRE mask encoding (NEW)
    # -------------------------
    def _encode_fire_mask(self, mask: int) -> bytes:
        """
        Encode a FIRE mask as 1..8 bytes, big-endian, minimal length.
        Supports up to 64 cues (64 bits). Backward compatible with 1-byte masks.
        """
        if mask is None:
            raise NFCPError("Mask is None")

        mask &= 0xFFFFFFFFFFFFFFFF

        # Always at least 1 byte
        if mask == 0:
            return b"\x00"

        nbytes = 1
        tmp = mask
        while tmp > 0xFF:
            nbytes += 1
            tmp >>= 8

        if nbytes > 8:
            raise NFCPError("Mask too large (>64-bit)")
        return mask.to_bytes(nbytes, "big")

    # Broadcast commands that do not target a single module.
    def build_broadcast(self, cmd: int, data: bytes = b"") -> bytes:
        if cmd not in (CMD_ARM, CMD_DISARM, CMD_DISCOVER, CMD_KEEPALIVE, CMD_SCRIPT_START, CMD_SCRIPT_STOP, CMD_SCRIPT_HEARTBEAT, CMD_TIME_SYNC, CMD_PAIR):
            raise NFCPError("Broadcast only allowed for ARM/DISARM/DISCOVER/KEEPALIVE/SCRIPT")
        return self._frame(SOF_BROADCAST, bytes([self.node_id, cmd]) + data)

    def build_arm_broadcast(self) -> bytes:
        return self.build_broadcast(CMD_ARM)

    def build_disarm_broadcast(self) -> bytes:
        return self.build_broadcast(CMD_DISARM)

    def build_discover_broadcast(self, nonce=None) -> bytes:
        if nonce is None:
            return self.build_broadcast(CMD_DISCOVER)
        return self.build_broadcast(CMD_DISCOVER, (nonce & 0xFFFF).to_bytes(2, "big"))

    def build_keepalive_broadcast(self, master_ticks_ms=None) -> bytes:
        if master_ticks_ms is None:
            return self.build_broadcast(CMD_KEEPALIVE)
        return self.build_broadcast(CMD_KEEPALIVE, (master_ticks_ms & 0xFFFFFFFF).to_bytes(4, "big"))

    # FIRE (group/unique only) ? mask is now 1..8 bytes
    def build_group_fire(self, group_addr: int, mask: int) -> bytes:
        mask_bytes = self._encode_fire_mask(mask)
        payload = (
            bytes([self.node_id]) +
            (group_addr & 0xFFFF).to_bytes(2, "big") +
            bytes([CMD_FIRE]) +
            mask_bytes
        )
        return self._frame(SOF_GROUP, payload)

    def build_unique_fire(self, target_unique: int, mask: int) -> bytes:
        mask_bytes = self._encode_fire_mask(mask)
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_FIRE]) +
            mask_bytes
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_unique_command(self, target_unique: int, cmd: int, data: bytes = b"") -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([cmd & 0xFF]) +
            data
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_arm_unique(self, target_unique: int) -> bytes:
        return self.build_unique_command(target_unique, CMD_ARM)

    def build_disarm_unique(self, target_unique: int) -> bytes:
        return self.build_unique_command(target_unique, CMD_DISARM)

    # Setters (unique only)
    def build_set_group(self, target_unique: int, new_group: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SET_GROUP]) +
            (new_group & 0xFFFF).to_bytes(2, "big")
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_set_unique(self, target_unique: int, new_unique: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SET_UNIQUE]) +
            (new_unique & 0xFFFFFFFF).to_bytes(4, "big")
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_set_network(self, target_unique: int, new_network: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SET_NETWORK]) +
            (new_network & 0xFFFF).to_bytes(2, "big")
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_set_radio_channel(self, target_unique: int, new_channel: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SET_RADIO_CHANNEL]) +
            bytes([new_channel & 0xFF])
        )
        return self._frame(SOF_UNIQUE, payload)

    # continuity request (unique only)
    def build_continuity_request(self, target_unique: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_CONTINUITY_REQ])
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_resistance_request(self, target_unique: int, raw_adc: bool = False) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_RESISTANCE_REQ]) +
            (bytes([RESISTANCE_REQUEST_RAW_ADC]) if raw_adc else b"")
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_output_status_request(self, target_unique: int) -> bytes:
        return self.build_unique_command(target_unique, CMD_OUTPUT_STATUS_REQ)

    def build_set_resistance_calibration(self, target_unique: int, bank_index: int, points) -> bytes:
        data = bytearray((
            RESISTANCE_CALIBRATION_VERSION,
            int(bank_index) & 0xFF,
            len(points) & 0xFF,
        ))
        for adc_mv, deci_ohms in points:
            data += (int(adc_mv) & 0xFFFF).to_bytes(2, "big")
            data += (int(deci_ohms) & 0xFFFF).to_bytes(2, "big")
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SET_RESISTANCE_CAL]) +
            bytes(data)
        )
        return self._frame(SOF_UNIQUE, payload)

    # ping / speed test (unique only)
    def build_ping(self, target_unique: int, data: bytes = b"") -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_PING]) +
            data
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_info_request(self, target_unique: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_INFO_REQ])
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_ota_update_begin(self, target_unique: int, transfer_id: int, path: str, size: int, checksum: int) -> bytes:
        path_bytes = path.encode()
        if len(path_bytes) > 64:
            raise NFCPError("OTA update path too long")
        data = (
            (transfer_id & 0xFFFF).to_bytes(2, "big") +
            (size & 0xFFFFFFFF).to_bytes(4, "big") +
            (checksum & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([len(path_bytes) & 0xFF]) +
            path_bytes
        )
        return self.build_unique_command(target_unique, CMD_OTA_UPDATE_BEGIN, data)

    def build_ota_update_write(self, target_unique: int, transfer_id: int, offset: int, chunk: bytes) -> bytes:
        data = (
            (transfer_id & 0xFFFF).to_bytes(2, "big") +
            (offset & 0xFFFFFFFF).to_bytes(4, "big") +
            chunk
        )
        return self.build_unique_command(target_unique, CMD_OTA_UPDATE_WRITE, data)

    def build_ota_update_end(self, target_unique: int, transfer_id: int) -> bytes:
        return self.build_unique_command(
            target_unique,
            CMD_OTA_UPDATE_END,
            (transfer_id & 0xFFFF).to_bytes(2, "big")
        )

    def build_ota_update_abort(self, target_unique: int, transfer_id: int = 0) -> bytes:
        return self.build_unique_command(
            target_unique,
            CMD_OTA_UPDATE_ABORT,
            (transfer_id & 0xFFFF).to_bytes(2, "big")
        )

    def build_ota_update_reboot(self, target_unique: int) -> bytes:
        return self.build_unique_command(target_unique, CMD_OTA_UPDATE_REBOOT)

    def build_script_clear(self, target_unique: int, script_id: int) -> bytes:
        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SCRIPT_CLEAR]) +
            (script_id & 0xFFFF).to_bytes(2, "big")
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_script_load(self, target_unique: int, script_id: int, chunk_index: int, entries) -> bytes:
        data = bytearray()
        data += (script_id & 0xFFFF).to_bytes(2, "big")
        data.append(chunk_index & 0xFF)
        data.append(len(entries) & 0xFF)

        for event_ms, cue_mask in entries:
            data += (int(event_ms) & 0xFFFFFFFF).to_bytes(4, "big")
            data += (int(cue_mask) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "big")

        payload = (
            bytes([self.node_id]) +
            (target_unique & 0xFFFFFFFF).to_bytes(4, "big") +
            bytes([CMD_SCRIPT_LOAD]) +
            bytes(data)
        )
        return self._frame(SOF_UNIQUE, payload)

    def build_time_sync_broadcast(self, master_ticks_ms: int) -> bytes:
        return self.build_broadcast(CMD_TIME_SYNC, (master_ticks_ms & 0xFFFFFFFF).to_bytes(4, "big"))

    def build_script_start_broadcast(self, script_id: int, start_delay_ms: int, offset_ms: int = 0, start_master_ms=None) -> bytes:
        data = (
            (script_id & 0xFFFF).to_bytes(2, "big") +
            (start_delay_ms & 0xFFFF).to_bytes(2, "big") +
            (offset_ms & 0xFFFFFFFF).to_bytes(4, "big")
        )
        if start_master_ms is not None:
            data += (int(start_master_ms) & 0xFFFFFFFF).to_bytes(4, "big")
        return self.build_broadcast(CMD_SCRIPT_START, data)

    def build_script_start(self, target_unique: int, script_id: int, start_delay_ms: int, offset_ms: int = 0, start_master_ms=None) -> bytes:
        data = (
            (script_id & 0xFFFF).to_bytes(2, "big") +
            (start_delay_ms & 0xFFFF).to_bytes(2, "big") +
            (offset_ms & 0xFFFFFFFF).to_bytes(4, "big")
        )
        if start_master_ms is not None:
            data += (int(start_master_ms) & 0xFFFFFFFF).to_bytes(4, "big")
        return self.build_unique_command(target_unique, CMD_SCRIPT_START, data)

    def build_script_stop_broadcast(self, script_id: int = 0) -> bytes:
        return self.build_broadcast(CMD_SCRIPT_STOP, (script_id & 0xFFFF).to_bytes(2, "big"))

    def build_script_stop(self, target_unique: int, script_id: int = 0) -> bytes:
        return self.build_unique_command(
            target_unique,
            CMD_SCRIPT_STOP,
            (script_id & 0xFFFF).to_bytes(2, "big")
        )

    def build_script_heartbeat_broadcast(self, script_id: int, elapsed_ms: int = 0) -> bytes:
        data = (
            (script_id & 0xFFFF).to_bytes(2, "big") +
            (elapsed_ms & 0xFFFFFFFF).to_bytes(4, "big")
        )
        return self.build_broadcast(CMD_SCRIPT_HEARTBEAT, data)

    def build_script_heartbeat(self, target_unique: int, script_id: int, elapsed_ms: int = 0) -> bytes:
        data = (
            (script_id & 0xFFFF).to_bytes(2, "big") +
            (elapsed_ms & 0xFFFFFFFF).to_bytes(4, "big")
        )
        return self.build_unique_command(target_unique, CMD_SCRIPT_HEARTBEAT, data)

    def build_script_step(self, target_unique: int, script_id: int, event_ms: int) -> bytes:
        data = (
            (script_id & 0xFFFF).to_bytes(2, "big") +
            (event_ms & 0xFFFFFFFF).to_bytes(4, "big")
        )
        return self.build_unique_command(target_unique, CMD_SCRIPT_STEP, data)

    # Response builder
    def build_response(self, src_unique: int, cmd: int, data: bytes = b"") -> bytes:
        payload = (src_unique & 0xFFFFFFFF).to_bytes(4, "big") + bytes([cmd & 0xFF]) + data
        return self._frame(SOF_RESPONSE, payload)

    def build_ack(self, src_unique: int, orig_cmd: int, code: int = ACK_OK) -> bytes:
        return self.build_response(src_unique, CMD_ACK, bytes([orig_cmd & 0xFF, code & 0xFF]))

    def build_nack(self, src_unique: int, orig_cmd: int, code: int) -> bytes:
        return self.build_response(src_unique, CMD_NACK, bytes([orig_cmd & 0xFF, code & 0xFF]))

    def build_discover_response(
        self,
        src_unique: int,
        group_id: int,
        unique_id: int,
        cue_count: int,
        device_type: int,
        proto_ver: int = 0x10,
        flags: int = 0x00,
        nonce=None,
    ) -> bytes:
        data = bytearray()
        data += (group_id & 0xFFFF).to_bytes(2, "big")
        data += (unique_id & 0xFFFFFFFF).to_bytes(4, "big")
        data.append(cue_count & 0xFF)
        data.append(device_type & 0xFF)
        data.append(proto_ver & 0xFF)
        data.append(flags & 0xFF)
        if nonce is not None:
            data += (nonce & 0xFFFF).to_bytes(2, "big")
        return self.build_response(src_unique, CMD_DISCOVER_RSP, bytes(data))

    # continuity response
    def build_continuity_response(self, src_unique: int, mask_or_cue_ok: int, openload_or_cue_count: int = None, mode: int = None) -> bytes:
        """Build a continuity response.

        Supports two call styles:

          New variable-length maindummy.py style:
            build_continuity_response(src_unique, open_mask, cue_count)

            Data payload is open_mask encoded as ceil(cue_count / 8) bytes,
            big-endian.  bit0=cue1, bit63=cue64, 1=open, 0=OK/closed.

          Legacy 16-cue style:
            build_continuity_response(src_unique, cue_ok_mask, openload16, mode)

            Data payload is cue_ok_mask(1) | openload16(2) | mode(1).
        """
        # New variable-length continuity format used by the uploaded maindummy.py.
        if mode is None:
            if openload_or_cue_count is None:
                raise NFCPError("cue_count required for variable-length continuity response")

            open_mask = mask_or_cue_ok & 0xFFFFFFFFFFFFFFFF
            cue_count = int(openload_or_cue_count)

            if cue_count < 1:
                cue_count = 1
            if cue_count > 64:
                cue_count = 64

            nbytes = (cue_count + 7) // 8
            if nbytes < 1:
                nbytes = 1
            if nbytes > 8:
                nbytes = 8

            # Keep only the bytes that are valid for the reported cue count.
            open_mask &= (1 << (nbytes * 8)) - 1
            data = open_mask.to_bytes(nbytes, "big")
            return self.build_response(src_unique, CMD_CONTINUITY_RSP, data)

        # Legacy 16-cue format.
        cue_ok_mask = mask_or_cue_ok
        openload16 = openload_or_cue_count
        data = bytes([cue_ok_mask & 0xFF]) + (openload16 & 0xFFFF).to_bytes(2, "big") + bytes([mode & 0xFF])
        return self.build_response(src_unique, CMD_CONTINUITY_RSP, data)

# -------------------------------------------------------------------
# Stream RX parser (consolidated from nfcp_rx.py)
# -------------------------------------------------------------------
# Literal 0xFF is encoded on the wire as: 0xFF 0xFE
ESC_0xFF_VALUE = 0xFE

WAIT_FF   = 0
WAIT_SOF2 = 1
RECV      = 2
WAIT_ESC2 = 3


class NFCPStreamParser:
    """Incremental byte-at-a-time NFCP frame parser.

    feed(b) returns a decoded packet dict or None until a full valid frame is received.
    Packet format matches main.py expectations:
      {"frame_type", "src", "target", "cmd", "data"}
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.state = WAIT_FF
        self.frame_type = None
        self.buf = bytearray()
        self.length = None
        self.expected = None

    def feed(self, b):
        # Wait for 0xFF
        if self.state == WAIT_FF:
            if b == ESC:
                self.state = WAIT_SOF2
            return None

        # Wait for SOF2 type
        if self.state == WAIT_SOF2:
            if b == SOF2_BROADCAST:
                self._start(SOF_BROADCAST); return None
            if b == SOF2_GROUP:
                self._start(SOF_GROUP); return None
            if b == SOF2_UNIQUE:
                self._start(SOF_UNIQUE); return None
            if b == SOF2_RESPONSE:
                self._start(SOF_RESPONSE); return None
            self.reset()
            return None

        # Receive stuffed body
        if self.state == RECV:
            if b == ESC:
                self.state = WAIT_ESC2
                return None
            self._push(b)
            return self._finish()

        # ESC seen inside body
        if self.state == WAIT_ESC2:
            # literal 0xFF byte
            if b == ESC_0xFF_VALUE:
                self._push(ESC)
                self.state = RECV
                return self._finish()

            # resync: treat as a new SOF2 mid-frame
            if b == SOF2_BROADCAST:
                self._start(SOF_BROADCAST); return None
            if b == SOF2_GROUP:
                self._start(SOF_GROUP); return None
            if b == SOF2_UNIQUE:
                self._start(SOF_UNIQUE); return None
            if b == SOF2_RESPONSE:
                self._start(SOF_RESPONSE); return None

            # invalid escape sequence
            self.reset()
            return None

        self.reset()
        return None

    def _start(self, frame_type):
        self.frame_type = frame_type
        self.state = RECV
        self.buf = bytearray()
        self.length = None
        self.expected = None

    def _push(self, b):
        # Body is unstuffed as it is pushed into buf:
        # First byte is LEN, then LEN payload bytes, then 2 CRC bytes.
        if self.length is None:
            self.length = b & 0xFF
            self.expected = self.length + 2  # payload + CRC16
            self.buf.append(self.length)
        else:
            self.buf.append(b)
            self.expected -= 1

    def _finish(self):
        if self.expected != 0:
            return None

        # buf = [LEN | PAYLOAD... | CRC16(2)]
        header = self.buf[:-2]  # LEN|PAYLOAD
        rx_crc = int.from_bytes(self.buf[-2:], "big")
        if crc16_ccitt(header) != rx_crc:
            self.reset()
            return None

        payload = header[1:]  # strip LEN
        pkt = self._decode(payload)
        self.reset()
        return pkt

    def _decode(self, payload):
        pkt = {"frame_type": self.frame_type, "src": None, "target": None, "cmd": None, "data": b""}

        if self.frame_type == SOF_BROADCAST:
            if len(payload) < 2:
                return None
            pkt["src"] = payload[0]
            pkt["cmd"] = payload[1]
            pkt["data"] = payload[2:]
            if pkt["cmd"] not in (CMD_ARM, CMD_DISARM, CMD_DISCOVER, CMD_KEEPALIVE, CMD_SCRIPT_START, CMD_SCRIPT_STOP, CMD_SCRIPT_HEARTBEAT, CMD_TIME_SYNC, CMD_PAIR):
                return None

        elif self.frame_type == SOF_GROUP:
            if len(payload) < 4:
                return None
            pkt["src"] = payload[0]
            pkt["target"] = int.from_bytes(payload[1:3], "big")
            pkt["cmd"] = payload[3]
            pkt["data"] = payload[4:]
            if pkt["cmd"] in (
                CMD_SET_GROUP,
                CMD_SET_UNIQUE,
                CMD_SET_NETWORK,
                CMD_SET_RADIO_CHANNEL,
                CMD_CONTINUITY_REQ,
                CMD_RESISTANCE_REQ,
                CMD_SET_RESISTANCE_CAL,
                CMD_OUTPUT_STATUS_REQ,
                CMD_FIRE_PULSE,
                CMD_INFO_REQ,
                CMD_OTA_UPDATE_BEGIN,
                CMD_OTA_UPDATE_WRITE,
                CMD_OTA_UPDATE_END,
                CMD_OTA_UPDATE_ABORT,
                CMD_OTA_UPDATE_REBOOT,
                CMD_SCRIPT_CLEAR,
                CMD_SCRIPT_LOAD,
                CMD_SCRIPT_STEP,
            ):
                return None

        elif self.frame_type == SOF_UNIQUE:
            if len(payload) < 6:
                return None
            pkt["src"] = payload[0]
            pkt["target"] = int.from_bytes(payload[1:5], "big")
            pkt["cmd"] = payload[5]
            pkt["data"] = payload[6:]

        elif self.frame_type == SOF_RESPONSE:
            if len(payload) < 5:
                return None
            pkt["src"] = int.from_bytes(payload[0:4], "big")
            pkt["cmd"] = payload[4]
            pkt["data"] = payload[5:]

        if pkt["cmd"] == CMD_FIRE and self.frame_type == SOF_BROADCAST:
            return None
        if pkt["cmd"] in (
            CMD_SET_GROUP,
            CMD_SET_UNIQUE,
            CMD_SET_NETWORK,
            CMD_SET_RADIO_CHANNEL,
            CMD_CONTINUITY_REQ,
            CMD_RESISTANCE_REQ,
            CMD_SET_RESISTANCE_CAL,
            CMD_OUTPUT_STATUS_REQ,
            CMD_FIRE_PULSE,
            CMD_INFO_REQ,
            CMD_OTA_UPDATE_BEGIN,
            CMD_OTA_UPDATE_WRITE,
            CMD_OTA_UPDATE_END,
            CMD_OTA_UPDATE_ABORT,
            CMD_OTA_UPDATE_REBOOT,
            CMD_SCRIPT_CLEAR,
            CMD_SCRIPT_LOAD,
            CMD_SCRIPT_STEP,
        ) and self.frame_type != SOF_UNIQUE:
            return None

        return pkt
