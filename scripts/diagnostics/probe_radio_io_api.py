import machine

print("BITSTREAM", hasattr(machine, "bitstream"))
print("SOFTSPI", hasattr(machine, "SoftSPI"))
try:
    import neopixel
    print("NEOPIXEL", hasattr(neopixel, "NeoPixel"))
except ImportError:
    print("NEOPIXEL", False)
try:
    import pyb
    print("PYB_NEOPIXEL", hasattr(pyb, "NeoPixel"))
except ImportError:
    print("PYB_NEOPIXEL", False)
