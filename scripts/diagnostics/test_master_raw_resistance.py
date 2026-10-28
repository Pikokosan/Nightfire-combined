"""Request one raw ADC resistance scan through the master."""

import __main__


print("RAW_RESULT", __main__.request_resistance_for_uid(0xFE81F019, 24, 5000, True))
print("RAW_PACKETS", __main__.get_last_packets())
