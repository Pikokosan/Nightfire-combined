# Nightfire mesh radio layer, v1 (design: docs/mesh_radio_design.md).
#
# Radio payload:
#   network_id(2) | ctl(1) | origin(4) | msg_id(2) | [relay(4)] | [age(2)] | NFCP frame
# ctl: bits 7-6 version, 5-4 mode, 3 relay field present, 2 age field present,
#      1-0 hops (transmissions so far minus one).
#
# Shared by the module firmware (through module_link.py) and both bridges.
# No imports beyond time: the bridges don't carry nfcp.py.
# The field image bundles this file into one namespace with field_main.py,
# so every top-level name here starts with MESH_/Mesh/mesh_.

import time

MESH_VERSION = 1
MESH_RADIO_ADDRESS = 0x6D657368      # "mesh"; pre-mesh firmware uses 0x67676767
MESH_DEFAULT_MAX_HOPS = 2            # bridge -> relay -> module
MESH_HOPS_LIMIT = 4                  # what the 2-bit hops field can carry

MESH_MODE_FLOOD = 0
MESH_MODE_DIRECT = 1
MESH_MODE_VIA = 2

MESH_ESC = 0xFF                      # NFCP frames start with ESC, SOF2
MESH_CTL_RELAY = 0x08
MESH_CTL_TIMED = 0x04
MESH_HEADER_LEN = 9

MESH_DEDUPE_WINDOW_MS = 2000
MESH_DEDUPE_LIMIT = 32

# Relay timing (managed flooding). A node waits before relaying; the wait
# is shorter the weaker it heard the frame, so nodes at the edge of coverage
# (whose relay reaches new ground) go first. If it hears MESH_SUPPRESS_COUNT
# relayed copies (hops >= 1) of the frame during its wait, it stays quiet.
MESH_RELAY_WAIT_MIN_MS = 1
MESH_RELAY_WAIT_SPAN_MS = 6          # added linearly from -95 dBm to -40 dBm
MESH_RELAY_JITTER_MS = 3             # plus random 0..2 ms
MESH_RELAY_RSSI_WEAK = -95
MESH_RELAY_RSSI_STRONG = -40
MESH_SUPPRESS_COUNT = 2
MESH_SUPPRESS_RESPONSES = False      # replies to the bridge always relay
MESH_RELAY_COPIES = 2
MESH_RELAY_GAP_MS = 6

# Time code. Frames carrying these NFCP commands are "timed": they carry an
# age field (100 us units) holding the time since the master stamped them,
# as of the moment the frame has arrived at the receiver. Each sender sets it
# just before transmitting, so bridge hold time, repeats and relay waits are
# all accounted for.
MESH_TIMED_CMDS = (0x72, 0x75)       # SCRIPT_START, TIME_SYNC
MESH_RAMP_US = 40                    # radio ramp-up before the first bit
MESH_BYTE_US = 8                     # 1 Mbit/s
MESH_FRAME_OVERHEAD = 8              # preamble, address, length, CRC bytes
MESH_RX_LATENCY_US = 150             # packet end -> receive() anchors it

# Reliable unicast (class R). The bridge keeps a route per module, learned
# from every frame it hears from it, and delivers a request on that route,
# retrying with the same msg_id until the module's reply (which reuses the
# request's msg_id) comes back. Modules keep their last replies, so a retry
# gets the cached reply instead of running the command again.
MESH_ROUTE_DIRECT = 0
MESH_ROUTE_VIA = 1
MESH_ROUTE_FLOOD = 2
MESH_ROUTE_FRESH_MS = 30000          # a learned route is trusted this long
MESH_ROUTE_DIRECT_DBM = -80          # weaker direct links use a relay or a flood
MESH_ROUTE_CLOSE_DBM = -60           # a direct link this strong beats a sticky relay
                                     # (a dummy at -81..-83 dBm lost replies, 2026-11-08)
MESH_ATTEMPT_MIN_MS = 250            # shortest wait for a reply per attempt
MESH_REPLY_CACHE = 4                 # replies a module keeps for retries
MESH_REPLY_KEEP_MS = 3000
MESH_REPLY_RESEND_MS = 150           # repeats of one attempt arrive faster



def mesh_rand(limit):
    if limit <= 0:
        return 0
    return (time.ticks_us() ^ (time.ticks_ms() << 3)) % limit


def mesh_build(network_id, origin, msg_id, frame, mode=MESH_MODE_FLOOD, hops=0, relay=None, age=None):
    ctl = (MESH_VERSION << 6) | ((mode & 3) << 4) | (hops & 3)
    head = bytearray(MESH_HEADER_LEN)
    head[0] = (network_id >> 8) & 0xFF
    head[1] = network_id & 0xFF
    head[3] = (origin >> 24) & 0xFF
    head[4] = (origin >> 16) & 0xFF
    head[5] = (origin >> 8) & 0xFF
    head[6] = origin & 0xFF
    head[7] = (msg_id >> 8) & 0xFF
    head[8] = msg_id & 0xFF
    if relay is not None:
        ctl |= MESH_CTL_RELAY
        head += (relay & 0xFFFFFFFF).to_bytes(4, "big")
    if age is not None:
        ctl |= MESH_CTL_TIMED
        head += (age & 0xFFFF).to_bytes(2, "big")
    head[2] = ctl
    return bytes(head) + frame


def mesh_parse(payload):
    """Header fields of a mesh payload: (ctl, origin, msg_id, relay, age,
    frame_offset), or None if it isn't a mesh v1 payload."""
    if len(payload) < MESH_HEADER_LEN + 2:
        return None
    ctl = payload[2]
    if (ctl >> 6) != MESH_VERSION:
        return None
    origin = (payload[3] << 24) | (payload[4] << 16) | (payload[5] << 8) | payload[6]
    msg_id = (payload[7] << 8) | payload[8]
    offset = MESH_HEADER_LEN
    relay = None
    age = None
    if ctl & MESH_CTL_RELAY:
        relay = (payload[9] << 24) | (payload[10] << 16) | (payload[11] << 8) | payload[12]
        offset = 13
    if ctl & MESH_CTL_TIMED:
        if len(payload) < offset + 2:
            return None
        age = (payload[offset] << 8) | payload[offset + 1]
        offset += 2
    if len(payload) < offset + 2:
        return None
    return ctl, origin, msg_id, relay, age, offset


def mesh_transmitter(parsed):
    """Who sent this copy over the air: the relay that forwarded it, or the
    originator. (A VIA frame names its relay before anyone has forwarded it,
    so the relay field only counts once HOPS is above 0.)"""
    if parsed[0] & 3 and parsed[3] is not None:
        return parsed[3]
    return parsed[1]


def mesh_deaf(deaf_to, parsed):
    """Bench test only (MESH_BENCH_DEAF_TO): True if this copy came straight
    from a node we pretend is out of range, so the frame must take a relay."""
    return bool(deaf_to) and mesh_transmitter(parsed) in deaf_to


def mesh_relay_wait_ms(rssi, rand_fn):
    """How long a relay waits before forwarding a frame heard at rssi dBm."""
    if rssi < MESH_RELAY_RSSI_WEAK:
        rssi = MESH_RELAY_RSSI_WEAK
    elif rssi > MESH_RELAY_RSSI_STRONG:
        rssi = MESH_RELAY_RSSI_STRONG
    span = MESH_RELAY_RSSI_STRONG - MESH_RELAY_RSSI_WEAK
    return (MESH_RELAY_WAIT_MIN_MS +
            (rssi - MESH_RELAY_RSSI_WEAK) * MESH_RELAY_WAIT_SPAN_MS // span +
            rand_fn(MESH_RELAY_JITTER_MS))


def mesh_is_timed(frame):
    """True if the NFCP frame is time code (MESH_TIMED_CMDS): broadcast or
    unique. Looks at the first few bytes only."""
    if len(frame) < 6 or frame[0] != MESH_ESC:
        return False
    head = frame[2:14]
    if b"\xff" in head:
        head = head.replace(b"\xff\xfe", b"\xff")
    if frame[1] == 0xFD:              # broadcast: LEN node cmd
        index = 2
    elif frame[1] == 0xFB:            # unique: LEN node uid(4) cmd
        index = 6
    else:
        return False
    return len(head) > index and head[index] in MESH_TIMED_CMDS


def mesh_transit_us(payload_len, cca_ms=0):
    """Time from 'about to send' to 'receiver has the frame': clear-channel
    check, ramp-up, airtime and the receiver's anchoring delay."""
    return (cca_ms * 1000 + MESH_RAMP_US +
            (MESH_FRAME_OVERHEAD + payload_len) * MESH_BYTE_US + MESH_RX_LATENCY_US)


def mesh_stamp(payload, stamp):
    """The payload with its age field set for sending now.

    stamp = (t0_us, base_units, transit_us): the frame's age was base_units
    at ticks_us() == t0_us, and transit_us is added for the hop itself."""
    if stamp is None or not (payload[2] & MESH_CTL_TIMED):
        return payload
    t0_us, base_units, transit_us = stamp
    age = base_units + (time.ticks_diff(time.ticks_us(), t0_us) + transit_us + 50) // 100
    if age > 0xFFFF:
        age = 0xFFFF
    offset = 13 if payload[2] & MESH_CTL_RELAY else MESH_HEADER_LEN
    return payload[:offset] + bytes(((age >> 8) & 0xFF, age & 0xFF)) + payload[offset + 2:]


def mesh_relay_copy(payload, parsed, relay_uid):
    """The payload as a relay forwards it: one more hop, relay = this node."""
    ctl, origin, msg_id, relay, age, offset = parsed
    out = bytearray(payload[:MESH_HEADER_LEN])
    # Keep version, mode and the age flag; set the relay flag and hops + 1.
    out[2] = (ctl & 0xF4) | MESH_CTL_RELAY | (((ctl & 3) + 1) & 3)
    out += (relay_uid & 0xFFFFFFFF).to_bytes(4, "big")
    if age is not None:
        out += (age & 0xFFFF).to_bytes(2, "big")
    out += payload[offset:]
    return bytes(out)


class MeshSeen:
    """(origin, msg_id) keys heard recently. check() returns True for a key
    already seen in the window, otherwise records it."""

    def __init__(self, window_ms=MESH_DEDUPE_WINDOW_MS, limit=MESH_DEDUPE_LIMIT):
        self.window_ms = window_ms
        self.limit = limit
        self.keys = {}

    def check(self, key, now):
        seen = self.keys.get(key)
        if seen is not None and time.ticks_diff(now, seen) < self.window_ms:
            return True
        self.keys[key] = now
        if len(self.keys) > self.limit:
            self.prune(now)
        return False

    def prune(self, now):
        keys = self.keys
        for key in list(keys):
            if time.ticks_diff(now, keys[key]) >= self.window_ms:
                del keys[key]
        while len(keys) > self.limit:
            oldest = None
            for key in keys:
                if oldest is None or time.ticks_diff(keys[oldest], keys[key]) > 0:
                    oldest = key
            del keys[oldest]


class MeshQueue:
    """Payloads waiting to be sent at a given time. The main loop calls
    pop_due() and sends what it returns, so repeats and relays never make
    the node sleep (a sleeping node can't hear anything)."""

    def __init__(self):
        self.items = []

    def push(self, payload, delay_ms, tries=0, stamp=None):
        """Queue payload to send in delay_ms. tries counts busy-channel
        attempts so far; stamp (see mesh_stamp) sets a timed frame's age at
        the moment it is sent."""
        self.items.append((time.ticks_add(time.ticks_ms(), max(0, int(delay_ms))), payload, tries, stamp))

    def pop_due(self, now):
        """The next due item as (due, payload, tries, stamp), or None."""
        items = self.items
        for i in range(len(items)):
            if time.ticks_diff(now, items[i][0]) >= 0:
                return items.pop(i)
        return None

    def wait_ms(self, now, default_ms):
        """Receive timeout that wakes the loop for the next queued send.
        Never 0: nrf_radio.recv(0) waits forever."""
        wait = default_ms
        for item in self.items:
            due = item[0]
            left = time.ticks_diff(due, now)
            if left < wait:
                wait = left
        return wait if wait > 1 else 1

    def clear(self):
        self.items = []

    def __len__(self):
        return len(self.items)


class MeshBridge:
    """Mesh side of a bridge: originates frames with repeats, filters what it
    hears, and hands the NFCP frames on.

    send(payload) -> bool is the radio send function (nrf_radio.send)."""

    def __init__(self, send, origin, repeats=3, gap_min_ms=8, gap_max_ms=12):
        self.radio_send = send
        self.origin = origin & 0xFFFFFFFF
        self.repeats = repeats
        self.gap_min_ms = gap_min_ms
        self.gap_max_ms = gap_max_ms
        self.queue = MeshQueue()
        self.seen = MeshSeen()
        # Random start, so a reboot doesn't reuse ids still in a module's window.
        self.msg_id = (time.ticks_us() ^ (origin & 0xFFFF)) & 0xFFFF or 1
        self.stat_sent = 0
        self.stat_echo = 0
        self.stat_duplicate = 0
        self.stat_legacy = 0
        # uid -> [direct_rssi, direct_ms, relay_uid, relay_ms, preferred]
        # preferred: the route that last worked (MESH_ROUTE_DIRECT / VIA) or
        # None. It sticks until a delivery on it fails, so a module doesn't
        # flip between a relay and a marginal direct link.
        self.routes = {}
        self.deliveries = []
        self.last_hops = 0           # hops of the frame receive() returned
        self.deaf_to = ()            # bench test: see mesh_deaf()
        self.stat_deaf = 0

    def next_msg_id(self):
        self.msg_id = (self.msg_id + 1) & 0xFFFF
        if not self.msg_id:
            self.msg_id = 1
        return self.msg_id

    def send_frame(self, network_id, frame, since_us=None):
        """Send an NFCP frame as class F: flooded, now plus queued repeats.

        Time code (TIME_SYNC, SCRIPT_START) is sent with an age field. since_us
        is when the bridge got the frame from the host (ticks_us), so its own
        handling time counts toward the age; it defaults to now."""
        return self._transmit(network_id, self.next_msg_id(), frame, MESH_MODE_FLOOD, None, since_us)

    def _transmit(self, network_id, msg_id, frame, mode, relay, since_us):
        stamp = None
        if mesh_is_timed(frame):
            payload = mesh_build(network_id, self.origin, msg_id, frame, mode=mode, relay=relay, age=0)
            t0 = time.ticks_us() if since_us is None else since_us
            stamp = (t0, 0, mesh_transit_us(len(payload)))
            ok = bool(self.radio_send(mesh_stamp(payload, stamp)))
        else:
            payload = mesh_build(network_id, self.origin, msg_id, frame, mode=mode, relay=relay)
            ok = bool(self.radio_send(payload))
        delay = 0
        span = self.gap_max_ms - self.gap_min_ms + 1
        for unused in range(self.repeats - 1):
            delay += self.gap_min_ms + mesh_rand(span)
            self.queue.push(payload, delay, 0, stamp)
        self.stat_sent += 1
        return ok

    # --- routes ------------------------------------------------------------
    def learn(self, origin, hops, relay, rssi, now):
        route = self.routes.get(origin)
        if route is None:
            if len(self.routes) >= 64:
                self.routes = {}
            route = self.routes[origin] = [None, None, None, None, None]
        if hops == 0:
            route[0] = rssi
            route[1] = now
            if route[4] == MESH_ROUTE_VIA and rssi is not None and rssi >= MESH_ROUTE_CLOSE_DBM:
                route[4] = None      # it came close: let the RSSI rules choose again
        elif hops == 1 and relay is not None:
            route[2] = relay
            route[3] = now
            # A relay carries it and the direct link is weak or silent: the
            # relay is the better route from now on.
            direct = route[1] is not None and time.ticks_diff(now, route[1]) < MESH_ROUTE_FRESH_MS
            if route[4] is None and not (direct and route[0] is not None and route[0] >= MESH_ROUTE_DIRECT_DBM):
                route[4] = MESH_ROUTE_VIA

    def route_for(self, uid, now):
        """(MESH_ROUTE_*, relay uid or None) for reaching uid."""
        route = self.routes.get(uid)
        if route is not None:
            direct = route[1] is not None and time.ticks_diff(now, route[1]) < MESH_ROUTE_FRESH_MS
            via = route[3] is not None and time.ticks_diff(now, route[3]) < MESH_ROUTE_FRESH_MS
            strong = route[0] is None or route[0] >= MESH_ROUTE_DIRECT_DBM
            preferred = route[4]
            if preferred == MESH_ROUTE_VIA and route[2] is not None:
                return MESH_ROUTE_VIA, route[2]
            if preferred == MESH_ROUTE_DIRECT and direct:
                return MESH_ROUTE_DIRECT, None
            if direct and strong:
                return MESH_ROUTE_DIRECT, None
            if via:
                return MESH_ROUTE_VIA, route[2]
        # A weak direct link and no known relay: flood, so any module that
        # hears the bridge can carry it (DIRECT would forbid relaying). The
        # relayed reply then teaches a VIA route for the next request.
        return MESH_ROUTE_FLOOD, None

    def forget(self, uid, kind):
        """A delivery on this route got no reply: stop trusting it."""
        route = self.routes.get(uid)
        if route is None:
            return
        if kind == MESH_ROUTE_DIRECT:
            route[1] = None
        elif kind == MESH_ROUTE_VIA:
            route[3] = None
        if route[4] == kind:
            route[4] = None

    # --- reliable delivery -------------------------------------------------
    def send_reliable(self, network_id, uid, frame, timeout_ms, tag, since_us=None):
        """Deliver an NFCP request to uid: the cached route once, then
        floods, each attempt with the same msg_id, until the reply arrives.
        poll() reports the outcome under tag."""
        now = time.ticks_ms()
        route = self.route_for(uid, now)
        if route[0] == MESH_ROUTE_FLOOD:
            plan = (route, route, route)
        else:
            # A route that just failed is forgotten, so don't try it again.
            plan = (route, (MESH_ROUTE_FLOOD, None), (MESH_ROUTE_FLOOD, None))
        delivery = {
            "tag": tag, "uid": uid & 0xFFFFFFFF, "id": self.next_msg_id(), "network": network_id,
            "frame": frame, "plan": plan, "step": -1, "since": since_us,
            "per": max(MESH_ATTEMPT_MIN_MS, int(timeout_ms) // len(plan)), "due": now, "done": None,
        }
        self.deliveries.append(delivery)
        self._attempt(delivery, now)
        return delivery

    def _attempt(self, delivery, now):
        delivery["step"] += 1
        kind, relay = delivery["plan"][delivery["step"]]
        mode = (MESH_MODE_DIRECT, MESH_MODE_VIA, MESH_MODE_FLOOD)[kind]
        self._transmit(delivery["network"], delivery["id"], delivery["frame"], mode,
                       relay if kind == MESH_ROUTE_VIA else None, delivery["since"])
        delivery["since"] = None     # later attempts age from when they go out
        delivery["due"] = time.ticks_add(now, delivery["per"])

    def poll(self):
        """Finished deliveries: a list of (tag, result bytes). result is
        status (0 delivered, 1 no reply), route of the reply (MESH_ROUTE_*),
        relay uid (4), attempts used."""
        if not self.deliveries:
            return ()
        now = time.ticks_ms()
        finished = []
        for delivery in list(self.deliveries):
            done = delivery["done"]
            if done is None and time.ticks_diff(now, delivery["due"]) >= 0:
                self.forget(delivery["uid"], delivery["plan"][delivery["step"]][0])
                if delivery["step"] + 1 < len(delivery["plan"]):
                    self._attempt(delivery, now)
                    continue
                done = (1, MESH_ROUTE_FLOOD, 0)
            elif done is None:
                continue
            self.deliveries.remove(delivery)
            status, kind, relay = done
            finished.append((delivery["tag"], bytes((
                status, kind,
                (relay >> 24) & 0xFF, (relay >> 16) & 0xFF, (relay >> 8) & 0xFF, relay & 0xFF,
                delivery["step"] + 1,
            ))))
        return finished

    def send_legacy(self, packet):
        """Accept a pre-mesh radio packet (network(2) [seq(1)] NFCP frame)
        from code that still builds those, and send it as a mesh frame."""
        if len(packet) < 4:
            return False
        network_id = (packet[0] << 8) | packet[1]
        offset = 2
        if packet[2] != MESH_ESC and packet[3] == MESH_ESC:
            offset = 3
        return self.send_frame(network_id, packet[offset:])

    def service(self, send=None):
        now = time.ticks_ms()
        send = send or self.radio_send
        while True:
            item = self.queue.pop_due(now)
            if item is None:
                return
            send(mesh_stamp(item[1], item[3]))

    def wait_ms(self, default_ms):
        now = time.ticks_ms()
        wait = self.queue.wait_ms(now, default_ms)
        for delivery in self.deliveries:
            left = time.ticks_diff(delivery["due"], now)
            if left < wait:
                wait = left if left > 1 else 1
        return wait

    def receive(self, payload, network_id, rssi=None):
        """The NFCP frame of a new mesh payload from another node, or None.
        Cheap: no NFCP decoding, so the bridge keeps up with bursts."""
        if len(payload) < MESH_HEADER_LEN + 2:
            return None
        if payload[0] != ((network_id >> 8) & 0xFF) or payload[1] != (network_id & 0xFF):
            return None
        parsed = mesh_parse(payload)
        if parsed is None:
            self.stat_legacy += 1
            return None
        if mesh_deaf(self.deaf_to, parsed):
            self.stat_deaf += 1
            return None
        origin = parsed[1]
        if origin == self.origin:
            self.stat_echo += 1      # our own frame, relayed back
            return None
        now = time.ticks_ms()
        hops = parsed[0] & 3
        # Every copy teaches a route, duplicates included (a direct copy can
        # arrive after a relayed one).
        self.learn(origin, hops, parsed[3], rssi, now)
        for delivery in self.deliveries:
            if delivery["done"] is None and delivery["uid"] == origin and delivery["id"] == parsed[2]:
                sent, sent_relay = delivery["plan"][delivery["step"]]
                if sent != MESH_ROUTE_FLOOD:
                    # The route we chose carried it, whichever copy of the
                    # reply arrived first (a VIA target's own transmission is
                    # often heard directly before the relay forwards it).
                    delivery["done"] = (0, sent, sent_relay or 0)
                    route = self.routes.get(origin)
                    if route is not None:
                        route[4] = sent
                elif hops == 0:
                    delivery["done"] = (0, MESH_ROUTE_DIRECT, 0)
                elif hops == 1 and parsed[3] is not None:
                    delivery["done"] = (0, MESH_ROUTE_VIA, parsed[3])
                else:
                    delivery["done"] = (0, MESH_ROUTE_FLOOD, 0)
        if self.seen.check((origin << 16) | parsed[2], now):
            self.stat_duplicate += 1
            return None
        self.last_hops = hops
        return payload[parsed[5]:]
