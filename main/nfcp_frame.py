# NFCP frame encoding and decoding for the bridges.
#
# The module firmware uses nfcp.py. The bridges carry only this file, which
# is much smaller: the frame envelope, the command numbers the bridges check,
# and the pairing network. The values are nfcp.py's;
# scripts/tests/test_nfcp_frame.py checks that they match.
#
# Frame: ESC kind | escaped( length(1) payload CRC16(2) ), where every 0xFF
# after the kind byte is sent as ESC, NFCP_ESC_FF. The CRC is CCITT over
# length and payload. Payloads by kind:
#   broadcast  node_id(1) cmd(1) data
#   unique     node_id(1) uid(4) cmd(1) data
#   response   uid(4) cmd(1) data
#
# Every top-level name starts with NFCP_ or nfcp_: the USB master bundle puts
# this file in one namespace with bridge_radio.py, whose CMD_ names are
# bridge (USB) commands.

import bridge_radio

NFCP_ESC = 0xFF
NFCP_ESC_FF = 0xFE           # ESC, ESC_FF stands for a 0xFF byte

NFCP_KIND_BROADCAST = 0xFD   # nfcp.SOF2_BROADCAST
NFCP_KIND_UNIQUE = 0xFB      # nfcp.SOF2_UNIQUE
NFCP_KIND_RESPONSE = 0xFA    # nfcp.SOF2_RESPONSE

NFCP_MASTER_NODE_ID = 0x01   # node_id of frames from a master
# Longest frame the bridge sends for the app (a CMD_SEND_NFCP payload), and
# the longest it hands on (fits a CMD_NFCP_RX payload after rssi(2) hops(1)).
NFCP_MAX_TX_FRAME = 218
NFCP_MAX_RX_FRAME = 217

NFCP_CMD_ARM = 0x10
NFCP_CMD_DISARM = 0x11
NFCP_CMD_FIRE = 0x20
NFCP_CMD_CONTINUITY_REQ = 0x22
NFCP_CMD_RESISTANCE_REQ = 0x24
NFCP_CMD_SET_RESISTANCE_CAL = 0x26
NFCP_CMD_OUTPUT_STATUS_REQ = 0x27
NFCP_CMD_FIRE_PULSE = 0x29
NFCP_CMD_SET_UNIQUE = 0x30
NFCP_CMD_SET_GROUP = 0x31
NFCP_CMD_SET_NETWORK = 0x32
NFCP_CMD_SET_RADIO_CHANNEL = 0x33
NFCP_CMD_PAIR = 0x34
NFCP_CMD_DISCOVER = 0x40
NFCP_CMD_ACK = 0x50
NFCP_CMD_INFO_REQ = 0x52
NFCP_CMD_KEEPALIVE = 0x54
NFCP_CMD_OTA_UPDATE_BEGIN = 0x60
NFCP_CMD_OTA_UPDATE_WRITE = 0x61
NFCP_CMD_OTA_UPDATE_END = 0x62
NFCP_CMD_OTA_UPDATE_ABORT = 0x63
NFCP_CMD_OTA_UPDATE_REBOOT = 0x64
NFCP_CMD_SCRIPT_CLEAR = 0x70
NFCP_CMD_SCRIPT_LOAD = 0x71
NFCP_CMD_SCRIPT_START = 0x72
NFCP_CMD_SCRIPT_STOP = 0x73
NFCP_CMD_SCRIPT_HEARTBEAT = 0x74
NFCP_CMD_TIME_SYNC = 0x75
NFCP_CMD_SCRIPT_STEP = 0x76
NFCP_CMD_PING = 0x7F
NFCP_SCRIPT_ENTRY_BYTES = 12
NFCP_ACK_OK = 0x00

# Modules in pairing mode listen here (nfcp.PAIR_NETWORK_ID/PAIR_RADIO_CHANNEL).
NFCP_PAIR_NETWORK_ID = 0x7A1B
NFCP_PAIR_RADIO_CHANNEL = 80


def nfcp_valid_channel(channel):
    """A radio channel modules and bridges accept: 0, or 74-100."""
    return channel == 0 or 74 <= channel <= 100


def nfcp_decode(frame, kinds):
    """(kind, payload) of a whole NFCP frame whose kind is in kinds, or None
    if it is malformed or its CRC is wrong."""
    if len(frame) < 7 or len(frame) > NFCP_MAX_TX_FRAME or frame[0] != NFCP_ESC:
        return None
    kind = frame[1]
    if kind not in kinds:
        return None
    body = bytearray()
    escaped = False
    for value in frame[2:]:
        if escaped:
            if value != NFCP_ESC_FF:
                return None
            body.append(NFCP_ESC)
            escaped = False
        elif value == NFCP_ESC:
            escaped = True
        else:
            body.append(value)
    if escaped or len(body) < 5 or len(body) != body[0] + 3:
        return None
    if bridge_radio.crc16_ccitt(body[:-2]) != bridge_radio.int_from_be(body[-2:]):
        return None
    return kind, body[1:-2]


def nfcp_encode(kind, payload):
    """The NFCP frame carrying payload."""
    body = bytes([len(payload)]) + payload
    body += bridge_radio.int_to_be(bridge_radio.crc16_ccitt(body), 2)
    frame = bytearray((NFCP_ESC, kind))
    for value in body:
        frame.append(value)
        if value == NFCP_ESC:
            frame.append(NFCP_ESC_FF)
    return bytes(frame)


def nfcp_broadcast(command, data=b''):
    """A broadcast command from the master, as nfcp.NFCP.build_broadcast."""
    return nfcp_encode(NFCP_KIND_BROADCAST, bytes((NFCP_MASTER_NODE_ID, command)) + data)
