"""Simulated audio box for scripts/sim/mesh_sim.py (kind "audio").

Not device firmware: it stands in for the audio box of
docs/audio_sync_design.md so the field side can be tested before the box
exists. It plays a "track" on its own clock (with the node's drift) and
broadcasts SHOW_TIME every SHOW_TIME_GAP_MS (250) while it has a track loaded,
using the real mesh code, so the frames carry the mesh age field like the
bridge's time code.

The scenario drives it through PLAYER (play, pause, resume, jump, stop,
silence). Each change is printed, so the scenario can find its sim time.
"""
import time

import bridge_radio
import mesh
import nfcp
import nrf_radio

try:
    import nrf_node_config as audio_config
except ImportError:
    audio_config = None

SHOW_TIME_GAP_MS = getattr(audio_config, "SHOW_TIME_GAP_MS", 250)
SHOW_TIME_REPEATS = getattr(audio_config, "SHOW_TIME_REPEATS", 3)
NETWORK_ID = bridge_radio.DEFAULT_NETWORK_ID
RADIO_CHANNEL = 90


class Player:
    def __init__(self):
        self.track = 0
        self.state = nfcp.SHOW_STOPPED
        self.base_ms = 0          # position when started_us was taken
        self.started_us = 0
        self.silent = False       # stop sending, as if the box died
        self.requests = []        # filled by the scenario, run by main()

    def position_ms(self, now_us):
        if self.state != nfcp.SHOW_PLAYING:
            return self.base_ms
        return self.base_ms + time.ticks_diff(now_us, self.started_us) // 1000

    def apply(self, request):
        name = request[0]
        now = time.ticks_us()
        if name == "play":
            self.track = request[1]
            self.base_ms = 0
            self.started_us = now
            self.state = nfcp.SHOW_PLAYING
        elif name == "pause":
            self.base_ms = self.position_ms(now)
            self.state = nfcp.SHOW_PAUSED
        elif name == "resume":
            self.started_us = now
            self.state = nfcp.SHOW_PLAYING
        elif name == "jump":
            self.base_ms = self.position_ms(now) + request[1]
            self.started_us = now
        elif name == "end":
            self.base_ms = self.position_ms(now)
            self.state = nfcp.SHOW_ENDED
        elif name == "silence":
            self.silent = True
        print("AUDIO %s %s pos=%d" % (name, " ".join(str(a) for a in request[1:]), self.base_ms))


PLAYER = Player()


def main():
    nrf_radio.init(channel=RADIO_CHANNEL, tx_power=8, address=mesh.MESH_RADIO_ADDRESS)
    link = mesh.MeshBridge(nrf_radio.send, nrf_radio.device_id32(), repeats=SHOW_TIME_REPEATS)
    builder = nfcp.NFCP(node_id=0x01)
    next_ms = time.ticks_ms()
    print("AUDIO box %08X ready" % nrf_radio.device_id32())
    while True:
        while PLAYER.requests:
            PLAYER.apply(PLAYER.requests.pop(0))
        now = time.ticks_ms()
        if (PLAYER.track and not PLAYER.silent and PLAYER.state != nfcp.SHOW_STOPPED and
                time.ticks_diff(now, next_ms) >= 0):
            next_ms = time.ticks_add(now, SHOW_TIME_GAP_MS)
            read_us = time.ticks_us()
            frame = builder.build_show_time_broadcast(PLAYER.track, PLAYER.position_ms(read_us), PLAYER.state)
            # since_us: the age counts from when the position was read.
            link.send_frame(NETWORK_ID, frame, since_us=read_us)
            if PLAYER.state == nfcp.SHOW_ENDED:
                PLAYER.state = nfcp.SHOW_STOPPED
        link.service()
        wait = time.ticks_diff(next_ms, time.ticks_ms())
        nrf_radio.recv(link.wait_ms(wait if 1 <= wait <= 20 else 20))
