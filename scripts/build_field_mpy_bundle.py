from __future__ import print_function

import argparse
import io
import os


LOCAL_IMPORTS = {
    "module_ota": ["import module_ota\n"],
    "nrf_module_common": ["import nrf_module_common\n"],
    "analog_mux74hc4051": ["from analog_mux74hc4051 import HC4051Scanner\n"],
    "nfcp": ["from nfcp import *\n"],
    "shift_register74hc595": [
        "from shift_register74hc595 import ShiftRegister74HC59524\n"
    ],
}


def read_text(path):
    with io.open(path, "r", encoding="utf-8") as source:
        return source.read()


def remove_exact(text, value, source_name):
    if value not in text:
        raise RuntimeError("Expected import was not found in %s: %r" % (source_name, value))
    return text.replace(value, "", 1)


def strip_class_methods(text, names):
    output = []
    skipping = False
    for line in text.splitlines(True):
        if line.startswith("    def "):
            method_name = line[8:].split("(", 1)[0]
            skipping = method_name in names
        elif skipping and line and line[0] not in " \t\r\n":
            skipping = False
        if not skipping:
            output.append(line)
    return "".join(output)


def strip_field_imports(text):
    for module_name, imports in LOCAL_IMPORTS.items():
        for import_line in imports:
            text = remove_exact(text, import_line, "field_main.py")

    measurement_import = (
        "from field_measurement import (\n"
        "    BatteryVoltageSensor,\n"
        "    ChannelCalibration,\n"
        "    ChannelClassifier,\n"
        "    ChannelSenseSensor,\n"
        "    PretestCueSenseSensor,\n"
        "    ResistanceCalibration,\n"
        "    STATE_FAULT,\n"
        "    STATE_NORMAL,\n"
        "    STATE_OPEN,\n"
        ")\n"
    )
    text = remove_exact(text, measurement_import, "field_main.py")

    lcd_import = (
        "try:\n"
        "    from lcd1602_i2c import I2cLcd1602\n"
        "except Exception:\n"
        "    I2cLcd1602 = None\n"
    )
    return remove_exact(text, lcd_import, "field_main.py")


def build_bundle(main_dir):
    nfcp = read_text(os.path.join(main_dir, "nfcp.py"))
    nfcp = strip_class_methods(nfcp, {
        "_encode_fire_mask",
        "build_broadcast",
        "build_arm_broadcast",
        "build_disarm_broadcast",
        "build_discover_broadcast",
        "build_keepalive_broadcast",
        "build_group_fire",
        "build_unique_fire",
        "build_unique_command",
        "build_arm_unique",
        "build_disarm_unique",
        "build_set_group",
        "build_set_unique",
        "build_set_network",
        "build_set_radio_channel",
        "build_continuity_request",
        "build_resistance_request",
        "build_set_resistance_calibration",
        "build_ping",
        "build_info_request",
        "build_ota_update_begin",
        "build_ota_update_write",
        "build_ota_update_end",
        "build_ota_update_abort",
        "build_ota_update_reboot",
        "build_script_clear",
        "build_script_load",
        "build_time_sync_broadcast",
        "build_script_start_broadcast",
        "build_script_start",
        "build_script_stop_broadcast",
        "build_script_stop",
        "build_script_heartbeat_broadcast",
        "build_script_heartbeat",
        "build_script_step",
        "build_continuity_response",
    })

    module_ota = read_text(os.path.join(main_dir, "module_ota.py"))
    module_ota = remove_exact(module_ota, "from nfcp import *\n", "module_ota.py")
    module_ota += (
        "\nclass _BundledModuleOta:\n"
        "    pass\n\n"
        "module_ota = _BundledModuleOta()\n"
        "module_ota.begin = begin\n"
        "module_ota.write = write\n"
        "module_ota.end = end\n"
        "module_ota.abort = abort\n"
        "module_ota.reboot = reboot\n"
        "module_ota.poll_timeout = poll_timeout\n"
    )

    common = read_text(os.path.join(main_dir, "nrf_module_common.py"))
    common += (
        "\nclass _BundledNrfModuleCommon:\n"
        "    pass\n\n"
        "nrf_module_common = _BundledNrfModuleCommon()\n"
        "nrf_module_common.valid_radio_channel = valid_radio_channel\n"
        "nrf_module_common.get_default_unique_id = get_default_unique_id\n"
        "nrf_module_common.send_with_cca = send_with_cca\n"
    )

    measurement = read_text(os.path.join(main_dir, "field_measurement.py"))
    measurement = remove_exact(
        measurement,
        "from adc_scaling import ADCScaler, VoltageCalibration\n",
        "field_measurement.py",
    )

    field_main = strip_field_imports(read_text(os.path.join(main_dir, "field_main.py")))

    parts = [
        "# Generated compact field-module image. Do not edit.\n",
        nfcp,
        module_ota,
        common,
        read_text(os.path.join(main_dir, "adc_scaling.py")),
        measurement,
        read_text(os.path.join(main_dir, "analog_mux74hc4051.py")),
        read_text(os.path.join(main_dir, "shift_register74hc595.py")),
        read_text(os.path.join(main_dir, "lcd1602_i2c.py")),
        field_main,
    ]
    return "\n\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    bundle = build_bundle(os.path.abspath(args.main_dir))
    with io.open(args.output, "w", encoding="utf-8", newline="\n") as output:
        output.write(bundle)
    print("Built %s (%d bytes)" % (args.output, len(bundle.encode("utf-8"))))


if __name__ == "__main__":
    main()
