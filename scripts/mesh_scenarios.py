"""Scenarios for scripts/mesh_sim.py.

Each scenario builds a topology and returns (script, duration_ms). The
script plays the app through the bridge and records checks.

The checks are the mesh goals: every module reachable, commands delivered,
fire latency, and script timing spread. Pre-mesh firmware is expected to
fail the checks for modules out of the bridge's direct range. Those
failures are the baseline that Phases 1-3 have to turn into passes.

Range guide with the default radio model (+8 dBm, path loss exponent 3,
sensitivity -93 dBm): about -80 dBm at 40 m, -87 dBm at 70 m, and -96 dBm
at 140 m (mostly out of range).
"""

from __future__ import print_function

import collections
import math

import nfcp
from mesh_sim import first_print, node_tx_after

FIRE_RE = r"^(?:DUMMY )?FIRE mask=0x"
SCRIPT_FIRE_RE = r"^SCRIPT FIRE mask=0x"
FIRE_RADIO_GOAL_MS = 15.0         # bridge starts sending -> module fires (2-hop goal)
SCRIPT_SPREAD_GOAL_MS = 5.0       # spread of one scripted cue across modules
SCRIPT_ID = 0x4D53


def modules_of(sim):
    return [n for n in sim.nodes.values() if n.kind != "bridge" and n.alive]


def standard_show(sim, app, modules=None, group=0x0001, label=""):
    """Discover, info, arm, unique fire, group fire, synced script, disarm."""
    modules = modules if modules is not None else modules_of(sim)
    prefix = (label + " ") if label else ""

    found = app.discover()
    for node in modules:
        sim.check(prefix + "discover %s" % node.name, node.device_id in found,
                  "rssi=%s" % found.get(node.device_id, "--"))

    for node in modules:
        pkt = app.info(node.device_id)
        sim.check(prefix + "info %s" % node.name, pkt is not None)

    armed = []
    for node in modules:
        ok = app.arm(node.device_id)
        sim.check(prefix + "arm %s" % node.name, ok)
        if ok:
            armed.append(node)

    # Unique fire, one cue per module; latency from the app and from the radio.
    for node in modules:
        sent = app.fire_unique(node.device_id, 0x1)
        frame = app.last_frame
        app.sleep(300)
        hit = first_print(node, FIRE_RE, sent)
        if hit:
            bridge_tx = node_tx_after(sim, "bridge", sent, contains=frame)
            latency = (hit[0] - sent) / 1000.0
            radio = (hit[0] - bridge_tx) / 1000.0 if bridge_tx is not None else float("nan")
            sim.check(prefix + "fire %s" % node.name, radio <= FIRE_RADIO_GOAL_MS,
                      "radio->fire %.1f ms (app->fire %.1f ms)" % (radio, latency))
        else:
            sim.check(prefix + "fire %s" % node.name, False,
                      "not fired" + ("" if node in armed else " (not armed)"))

    # Group fire: every module in the group should fire once.
    sent = app.fire_group(group, 0x2)
    app.sleep(400)
    times = {}
    for node in modules:
        hit = first_print(node, r"^(?:DUMMY )?FIRE mask=0x0*2\b", sent)
        if hit:
            times[node.name] = (hit[0] - sent) / 1000.0
    missing = [n.name for n in modules if n.name not in times]
    sim.check(prefix + "group fire", not missing,
              "fired %d/%d%s%s" % (
                  len(times), len(modules),
                  (" max %.1f ms" % max(times.values())) if times else "",
                  (" missing " + ",".join(missing)) if missing else ""))

    scripted_show(sim, app, modules, prefix)

    for node in modules:
        sim.check(prefix + "disarm %s" % node.name, app.disarm(node.device_id))


def scripted_show(sim, app, modules, prefix=""):
    """Upload a 2-cue script, start it on the master clock, and measure how
    closely the modules fire together."""
    entries = [(800, 0x4), (1600, 0x8)]
    for node in modules:
        uid = node.device_id
        cleared = app.ack(uid, app.master.build_script_clear(uid, SCRIPT_ID),
                          "script clear %s" % node.name, nfcp.CMD_SCRIPT_CLEAR)
        loaded = app.ack(uid, app.master.build_script_load(uid, SCRIPT_ID, 0, entries),
                         "script load %s" % node.name, nfcp.CMD_SCRIPT_LOAD)
        sim.check(prefix + "script upload %s" % node.name, cleared and loaded,
                  "" if cleared and loaded else "clear=%s load=%s" % (cleared, loaded))

    master_ms = int(app.now_ms()) & 0x3FFFFFFF
    app.broadcast(app.master.build_time_sync_broadcast(master_ms), "time sync %d" % master_ms)
    app.sleep(150)
    master_ms = int(app.now_ms()) & 0x3FFFFFFF
    start_ms = master_ms + 400
    started = app.broadcast(
        app.master.build_script_start_broadcast(SCRIPT_ID, 400, 0, start_ms),
        "script start at master %d" % start_ms)
    for i in range(6):
        app.sleep(400)
        app.broadcast(app.master.build_script_heartbeat_broadcast(SCRIPT_ID, int(app.now_ms()) - start_ms),
                      "script heartbeat")
    app.sleep(300)

    for event_ms, mask in entries:
        times = {}
        pattern = r"^SCRIPT FIRE mask=0x0*%X event=%d" % (mask, event_ms)
        for node in modules:
            hit = first_print(node, pattern, started)
            if hit:
                times[node.name] = hit[0] / 1000.0
        missing = [n.name for n in modules if n.name not in times]
        spread = (max(times.values()) - min(times.values())) if len(times) > 1 else 0.0
        # Absolute error against the master clock (true sim time of the cue).
        target = start_ms + event_ms
        sim.check(prefix + "script cue @%dms" % event_ms,
                  not missing and spread <= SCRIPT_SPREAD_GOAL_MS,
                  "fired %d/%d spread %.1f ms%s" % (
                      len(times), len(modules), spread,
                      (" missing " + ",".join(missing)) if missing else ""))
        if times:
            errors = ["%s%+.1f" % (name, t - target) for name, t in sorted(times.items())]
            sim.note(prefix + "cue @%dms vs master" % event_ms, "ms: " + " ".join(errors))


# ---------------------------------------------------------------------------
# Topologies
# ---------------------------------------------------------------------------
def star(sim):
    """Simulator sanity check: 4 dummies all in direct range of the bridge."""
    sim.add_bridge(pos=(0, 0))
    for i, pos in enumerate([(25, 0), (0, 30), (-35, 5), (20, -40)]):
        sim.add_module("m%d" % (i + 1), pos)
    return (lambda s, app: standard_show(s, app)), 90000


def chain(sim):
    """Bridge - A (70 m) - B (140 m): B only hears A. Needs one relay."""
    sim.add_bridge(pos=(0, 0))
    sim.add_module("A", (70, 0))
    sim.add_module("B", (140, 0))
    return (lambda s, app: standard_show(s, app)), 90000


def field_layout(sim):
    """8 modules: 4 near, 3 far (2 hops), 1 near but behind an obstacle."""
    sim.add_bridge(pos=(0, 0))
    layout = [
        ("n1", (40, 10)), ("n2", (55, -20)), ("n3", (45, 35)), ("n4", (65, 0)),
        ("f1", (120, 15)), ("f2", (130, -25)), ("f3", (110, 50)),
        ("w1", (50, -50)),
    ]
    for name, pos in layout:
        sim.add_module(name, pos)
    sim.obstacle("bridge", "w1", 25)
    return (lambda s, app: standard_show(s, app)), 180000


def heal(sim):
    """Two possible relays for B; relay R1 dies mid-show, R2 must take over."""
    sim.add_bridge(pos=(0, 0))
    sim.add_module("R1", (70, 20))
    sim.add_module("R2", (70, -20))
    sim.add_module("B", (140, 0))

    def script(s, app):
        standard_show(s, app, label="before")
        s.kill("R1")
        app.sleep(500)
        survivors = [s.nodes["R2"], s.nodes["B"]]
        standard_show(s, app, modules=survivors, label="after")

    return script, 240000


def deep(sim):
    """Bridge - A - B - C, 70 m apart: C is 3 hops out, past the default limit."""
    sim.add_bridge(pos=(0, 0))
    sim.add_module("A", (70, 0))
    sim.add_module("B", (140, 0))
    sim.add_module("C", (210, 0))

    def script(s, app):
        # MESH_MAX_HOPS=2 by default: A and B work, C must stay out of reach.
        standard_show(s, app, modules=[s.nodes["A"], s.nodes["B"]])
        found = app.discover(nonce=0x3333)
        s.check("C beyond the hop limit", s.nodes["C"].device_id not in found,
                "found" if s.nodes["C"].device_id in found else "not reached, as expected")

    return script, 120000


def deep3(sim):
    """The deep chain with MESH_MAX_HOPS=3 on every module, so C is reachable."""
    sim.add_bridge(pos=(0, 0))
    for name, x in (("A", 70), ("B", 140), ("C", 210)):
        sim.add_module(name, (x, 0), config={"MESH_MAX_HOPS": 3})
    return (lambda s, app: standard_show(s, app)), 150000


def crowd(sim):
    """12 dummies in direct range: discover replies and floods contend."""
    sim.add_bridge(pos=(0, 0))
    for i in range(12):
        angle = i * 30
        sim.add_module("c%02d" % (i + 1), (35 * math.cos(math.radians(angle)),
                                           35 * math.sin(math.radians(angle))))

    def script(s, app):
        found = app.discover()
        mods = modules_of(s)
        got = [n for n in mods if n.device_id in found]
        s.check("one discover finds all", len(got) == len(mods), "%d/%d" % (len(got), len(mods)))
        found2 = app.discover(nonce=0x2222)
        got2 = [n for n in mods if n.device_id in found2]
        s.check("second discover finds all", len(got2) == len(mods), "%d/%d" % (len(got2), len(mods)))
        for n in mods:
            app.arm(n.device_id)
        sent = app.fire_group(0x0001, 0x2)
        app.sleep(500)
        fired = [n for n in mods if first_print(n, FIRE_RE, sent)]
        s.check("group fire reaches all", len(fired) == len(mods), "%d/%d" % (len(fired), len(mods)))
        for n in mods:
            app.disarm(n.device_id)

    return script, 120000


def field_fw(sim):
    """Two field modules (field_main.py) and a dummy, all direct."""
    sim.add_bridge(pos=(0, 0))
    sim.add_module("F1", (30, 0), fw="field")
    sim.add_module("F2", (0, 40), fw="field")
    sim.add_module("D1", (-30, 10))
    return (lambda s, app: standard_show(s, app)), 120000


SCENARIOS = collections.OrderedDict([
    ("star", star),
    ("chain", chain),
    ("field", field_layout),
    ("heal", heal),
    ("deep", deep),
    ("deep3", deep3),
    ("crowd", crowd),
    ("field_fw", field_fw),
])
