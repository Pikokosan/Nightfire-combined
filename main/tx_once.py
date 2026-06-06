import time

import nfcp
from e77_gfsk import E77Gfsk


radio = E77Gfsk()
radio.init()
print("TX_ONCE_READY")
time.sleep_ms(900)

n = nfcp.NFCP(1)
frame = n.build_discover_response(0xE7700001, 0x0001, 0xE7700001, 16, 2, 0x10, 0)
payload = (0x04D2).to_bytes(2, "big") + frame
ok = radio.send(payload)
print("TX_ONCE_SENT", ok, len(payload), payload[:12])
