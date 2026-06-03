from machine import UART, Pin
import time
import e77_radio


class ModuleTransport:
    def __init__(
        self,
        uart_id,
        baudrate,
        tx_pin,
        rx_pin,
        invert=False,
        rs485_de_pin=None,
        tx_turnaround_ms=2,
        transport="e77",
        radio_bootloader_baud=115200,
    ):
        self.uart_id = uart_id
        self.baudrate = baudrate
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.invert = bool(invert)
        self.rs485_de_pin = rs485_de_pin
        self.tx_turnaround_ms = tx_turnaround_ms
        self.transport = transport
        self.radio_bootloader_baud = radio_bootloader_baud

        self.radio_seq = 1
        self.radio_parser = e77_radio.E77FrameParser()
        self.pending_nfcp = bytearray()
        self.uart = None
        self.de = None
        self.restore_uart()

    def restore_uart(self):
        self.uart = UART(
            self.uart_id,
            baudrate=self.baudrate,
            tx=Pin(self.tx_pin),
            rx=Pin(self.rx_pin),
            invert=(UART.INV_TX | UART.INV_RX) if self.invert else 0,
        )
        self.uart.read(1)
        self.radio_parser = e77_radio.E77FrameParser()

        if self.rs485_de_pin is not None and self.de is None:
            self.de = Pin(self.rs485_de_pin, Pin.OUT, value=0)

    def any(self):
        return bool(self.pending_nfcp) or self.uart.any()

    def read_byte(self):
        data = self.uart.read(1)
        if data:
            return data[0]
        return None

    def read_nfcp_bytes(self):
        if self.transport != "e77":
            b = self.read_byte()
            if b is None:
                return b""
            return bytes([b])

        if self.pending_nfcp:
            b = self.pending_nfcp[0]
            self.pending_nfcp = self.pending_nfcp[1:]
            return bytes([b])

        b = self.read_byte()
        if b is None:
            return b""
        return self.nfcp_bytes_from_uart_byte(b)

    def cache_radio_frame(self, frame):
        if frame.cmd != e77_radio.CMD_NFCP_RX:
            return

        rx_info = e77_radio.parse_nfcp_rx(frame.payload)
        if not rx_info:
            return

        self.pending_nfcp += rx_info.get("nfcp_frame", b"")

    def next_radio_seq(self):
        seq = self.radio_seq & 0xFF
        self.radio_seq = (self.radio_seq + 1) & 0xFF
        if self.radio_seq == 0:
            self.radio_seq = 1
        return seq

    def run_radio_update_if_requested(self, enabled=True, progress_prefix="E77 radio update"):
        if self.transport != "e77" or not enabled:
            return

        try:
            import e77_bootloader
        except Exception as e:
            print("E77 updater import failed:", e)
            return

        if not e77_bootloader.update_requested():
            return

        info = e77_bootloader.find_firmware()
        if not info:
            print("E77 radio update requested, but no firmware was found")
            return

        def progress(message):
            print("%s: %s" % (progress_prefix, message))

        try:
            result = e77_bootloader.flash_first_available(
                self.uart_id,
                self.tx_pin,
                self.rx_pin,
                app_baud=self.baudrate,
                boot_baud=self.radio_bootloader_baud,
                progress_cb=progress,
            )
            e77_bootloader.clear_update_request()
            print("E77 radio update complete:", result.get("path", ""))
        except Exception as e:
            print("E77 radio update failed:", e)
        finally:
            self.restore_uart()

    def radio_boot_ping(self, enabled=True):
        if self.transport != "e77" or not enabled:
            return

        seq = 0xA5
        self.uart.write(e77_radio.build_frame(seq, e77_radio.CMD_PING))
        deadline = time.ticks_add(time.ticks_ms(), 1000)

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if not self.uart.any():
                time.sleep_ms(5)
                continue

            data = self.uart.read(32) or b""
            for b in data:
                frame = self.radio_parser.feed(b)
                if frame and frame.seq == seq and frame.cmd == e77_radio.CMD_PONG:
                    print("E77 radio UART ping OK")
                    return

        print("E77 radio UART ping failed")

    def set_network(self, network_id, timeout_ms=1000):
        if self.transport != "e77":
            return True

        seq = 0xA6
        self.uart.write(e77_radio.build_set_network(seq, network_id))
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if not self.uart.any():
                time.sleep_ms(5)
                continue

            data = self.uart.read(32) or b""
            for b in data:
                frame = self.radio_parser.feed(b)
                if not frame:
                    continue
                if frame.seq != seq:
                    continue
                if frame.cmd == e77_radio.CMD_SET_NETWORK and not (frame.flags & e77_radio.FLAG_ERROR):
                    print("E77 radio network = 0x%04X" % (network_id & 0xFFFF))
                    return True

        print("E77 radio network set failed")
        return False

    def get_radio_info(self, timeout_ms=1000):
        if self.transport != "e77":
            return None

        seq = self.next_radio_seq()
        self.uart.write(e77_radio.build_get_info(seq))
        deadline = time.ticks_add(time.ticks_ms(), timeout_ms)

        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            if not self.uart.any():
                time.sleep_ms(5)
                continue

            data = self.uart.read(32) or b""
            for b in data:
                frame = self.radio_parser.feed(b)
                if not frame:
                    continue
                if frame.seq != seq:
                    continue
                if frame.cmd == e77_radio.CMD_GET_INFO and not (frame.flags & e77_radio.FLAG_ERROR):
                    return e77_radio.parse_get_info(frame.payload)

        return None

    def tx_bytes(self, data):
        if self.transport == "e77":
            for attempt in range(3):
                seq = self.next_radio_seq()
                self.uart.write(e77_radio.build_send_nfcp(seq, data))

                deadline = time.ticks_add(time.ticks_ms(), 250)
                while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                    if not self.uart.any():
                        time.sleep_ms(2)
                        continue

                    rx_data = self.uart.read(32) or b""
                    for b in rx_data:
                        frame = self.radio_parser.feed(b)
                        if not frame:
                            continue
                        if frame.cmd == e77_radio.CMD_NFCP_RX:
                            self.cache_radio_frame(frame)
                            continue
                        if frame.cmd == e77_radio.CMD_SEND_NFCP and frame.seq == seq:
                            if frame.payload and frame.payload[0] == 0:
                                return
                            break

                time.sleep_ms(20 + attempt * 20)

            print("E77 SEND_NFCP failed")
            return

        if self.de is not None:
            self.de.value(1)
            time.sleep_ms(1)

        self.uart.write(data)
        self.uart.flush()

        if self.de is not None:
            time.sleep_ms(self.tx_turnaround_ms)
            self.de.value(0)

    def nfcp_bytes_from_uart_byte(self, b):
        if self.transport != "e77":
            return bytes([b])

        frame = self.radio_parser.feed(b)
        if not frame:
            return b""
        if frame.cmd != e77_radio.CMD_NFCP_RX:
            return b""

        rx_info = e77_radio.parse_nfcp_rx(frame.payload)
        if not rx_info:
            return b""

        return rx_info.get("nfcp_frame", b"")
