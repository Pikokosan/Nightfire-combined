"""List a MicroPython device filesystem and report free space."""

import os


for name in os.listdir():
    try:
        size = os.stat(name)[6]
    except Exception:
        size = -1
    print("FILE", name, size)

try:
    stats = os.statvfs("/")
    print("FREE", stats[0] * stats[3], "TOTAL", stats[0] * stats[2])
except Exception as error:
    print("STATVFS", error)
