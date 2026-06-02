import os


class ModuleOtaSession:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.path = None
        self.tmp_path = None
        self.size = 0
        self.expected_checksum = 0
        self.offset = 0
        self.checksum = 0
        self.file = None

    def close_file(self):
        if self.file is not None:
            try:
                self.file.close()
            except:
                pass
        self.file = None

    def abort(self):
        tmp_path = self.tmp_path
        self.close_file()
        self.reset()
        if tmp_path:
            try:
                os.remove(tmp_path)
            except:
                pass

    def begin(self, path, size, checksum):
        if self.active:
            return False
        if not path or ".." in path:
            return False
        if path[0] != "/":
            path = "/" + path
        if size < 0:
            return False

        self.path = path
        self.tmp_path = path + ".ota"
        self.size = size & 0xFFFFFFFF
        self.expected_checksum = checksum & 0xFFFFFFFF
        self.offset = 0
        self.checksum = 0

        try:
            try:
                os.remove(self.tmp_path)
            except:
                pass
            self.file = open(self.tmp_path, "wb")
            self.active = True
            return True
        except:
            self.reset()
            return False

    def write(self, offset, chunk):
        if not self.active or self.file is None:
            return False
        if offset != self.offset:
            return False
        if self.offset + len(chunk) > self.size:
            return False

        try:
            self.file.write(chunk)
            for b in chunk:
                if not isinstance(b, int):
                    b = ord(b)
                self.checksum = (self.checksum + b) & 0xFFFFFFFF
            self.offset += len(chunk)
            return True
        except:
            return False

    def end(self):
        if not self.active:
            return False
        if self.offset != self.size:
            return False
        if self.checksum != self.expected_checksum:
            return False

        self.close_file()

        try:
            try:
                os.remove(self.path)
            except:
                pass
            os.rename(self.tmp_path, self.path)
            self.reset()
            return True
        except:
            self.abort()
            return False
