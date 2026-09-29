import time


source = open("main.py").read()
source = source.replace("LcdUiTest().run()", "")
scope = {}
exec(source, scope)

ui = scope["LcdUiTest"]()
print("created ui")
print("buttons:", [(b.name, b.pin_id, b.ok, b.error) for b in ui.buttons])
ui.init_lcd()
print("lcd:", ui.lcd is not None, "scan:", ["0x%02X" % a for a in ui.i2c_scan], "error:", ui.lcd_error)
line1, line2 = ui.page_lines()
print("page0:", line1, "|", line2)
ui.show(line1, line2)
time.sleep_ms(8000)
print("done")
