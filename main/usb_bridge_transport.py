import binascii
import select
import sys


class UsbHexTransport:
    PREFIX = "NFUSB1 "
    MAX_LINE = 512

    def __init__(self):
        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)
        self.line = ""
        self.discard = False
        self.pending = bytearray()

    def any(self):
        return bool(self.pending) or bool(self.poller.poll(0))

    def read(self, count):
        for unused in range(64):
            if not self.poller.poll(0):
                break
            character = sys.stdin.read(1)
            if character in ("\r", "\n"):
                if not self.discard and self.line.startswith(self.PREFIX):
                    try:
                        self.pending.extend(binascii.unhexlify(self.line[len(self.PREFIX):]))
                    except ValueError:
                        pass
                self.line = ""
                self.discard = False
            elif not self.discard:
                self.line += character
                if len(self.line) > self.MAX_LINE:
                    self.line = ""
                    self.discard = True
        result = self.pending[:count]
        self.pending = self.pending[count:]
        return result

    def write(self, data):
        sys.stdout.write(self.PREFIX + binascii.hexlify(data).decode() + "\n")
        return len(data)
