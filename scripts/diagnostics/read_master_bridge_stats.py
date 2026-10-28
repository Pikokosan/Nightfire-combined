"""Print the master bridge state without changing its saved configuration."""

import __main__


print("MASTER_NETWORK", "0x%04X" % (__main__.RADIO_NETWORK_ID & 0xFFFF))
print("MASTER_CHANNEL", __main__.RADIO_CHANNEL)
print("BRIDGE_STATS", __main__.get_local_radio_stats(2000))
