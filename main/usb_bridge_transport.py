import binascii
import select
import sys


class UsbHexTransport:
    """Bridge frames as hex text lines ("NFUSB1 <hex>") over USB stdio.

    read() takes every character already waiting, as bytes, so a whole
    command line is handled in one main-loop pass. On the nice!nano this
    takes about 5.6 ms for a 48-character line; the earlier reader (text
    characters, string building, at most 64 characters per pass) took about
    30 ms, and a longer line also waited for the next loop pass. It never
    blocks: a line cut off mid-way just waits for more characters, so the
    bridge's main loop (and its stop/disarm watchdog) keeps running.
    """

    PREFIX = b"NFUSB1 "
    MAX_LINE = 512

    def __init__(self):
        self.poller = select.poll()
        self.poller.register(sys.stdin, select.POLLIN)
        # Byte reads where stdio offers them (MicroPython); text otherwise.
        self.stream = getattr(sys.stdin, "buffer", None)
        self.line = bytearray()
        self.discard = False
        self.pending = bytearray()

    def any(self):
        return bool(self.pending) or bool(self.poller.poll(0))

    def _read_text_char(self, unused=1):
        character = sys.stdin.read(1)
        return character.encode() if character else b""

    def read(self, count):
        poll = self.poller.poll
        read_char = self.stream.read if self.stream is not None else self._read_text_char
        line = self.line
        prefix = self.PREFIX
        for unused in range(self.MAX_LINE + 64):
            if not poll(0):
                break
            character = read_char(1)
            if not character:
                break
            if character == b"\n" or character == b"\r":
                if not self.discard and line[:7] == prefix:
                    try:
                        self.pending.extend(binascii.unhexlify(line[7:]))
                    except ValueError:
                        pass
                line = bytearray()
                self.discard = False
            elif not self.discard:
                line += character
                if len(line) > self.MAX_LINE:
                    line = bytearray()
                    self.discard = True
        self.line = line
        result = self.pending[:count]
        self.pending = self.pending[count:]
        return result

    def write(self, data):
        sys.stdout.write(self.PREFIX.decode() + binascii.hexlify(data).decode() + "\n")
        return len(data)
