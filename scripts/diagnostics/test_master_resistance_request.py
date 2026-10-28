"""Device-side master probe for one field-module resistance response."""

import __main__


TARGET_UID = 0xFE81F019


result = __main__.request_resistance_for_uid(TARGET_UID, 24, 4000)
print("RES_RESULT", result)
