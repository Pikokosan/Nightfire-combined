import argparse
import datetime
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS / "lib"), str(SCRIPTS / "build")]

import serial

import raw_micropython_upload as raw


INTERFACE_SETTINGS = (b'ARM_SWITCH_PIN', b'STATUS_LED_PIN', b'STATUS_LED_COUNT',
                      b'STATUS_LED_ORDER', b'STATUS_LED_TIMING_NS',
                      b'STATUS_LED_POWER_PIN', b'STATUS_LED_POWER_ON_VALUE')


def prepare_master_config(device, repository):
    """Keep the bridge's own settings and enable master mode once. A bridge
    whose switch and LED pins are both unset takes the whole switch/LED block
    from the repository config; a bridge with its own pins keeps its block."""
    def setting(line):
        name, _, value = line.partition(b'=')
        return name.strip(), value.split(b'#')[0].strip()
    lines = [line for line in device.splitlines()
             if setting(line)[0] != b'MASTER_ENABLED']
    own = dict(setting(line) for line in lines if b'=' in line)
    if all(own.get(name, b'None') == b'None' for name in (b'ARM_SWITCH_PIN', b'STATUS_LED_PIN')):
        lines = [line for line in lines if setting(line)[0] not in INTERFACE_SETTINGS]
        lines += [line for line in repository.splitlines()
                  if setting(line)[0] in INTERFACE_SETTINGS]
    text = b'\n'.join(line.rstrip() for line in lines).strip()
    while b'\n\n\n' in text:
        text = text.replace(b'\n\n\n', b'\n\n')
    return text + b'\nMASTER_ENABLED = True\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    parser.add_argument("--master", action="store_true",
                        help="Install opt-in master mode with live controls and RF stop watchdog")
    parser.add_argument('--compiled', action='store_true',
                        help='Bundle master code with an installed compatible mpy_cross compiler')
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / "dist" / ("bridge_backup_" + stamp)
    backup.mkdir(parents=True)
    copies = {
        "main.py": root / "main/nrf_usb_monitor_main.py",
        "bridge_radio.py": root / "main/bridge_radio.py",
        "mesh.py": root / "main/mesh.py",
        "usb_bridge_transport.py": root / "main/usb_bridge_transport.py",
        "usb_monitor_nfcp.py": root / "main/usb_monitor_nfcp.py",
        "command_interface.py": root / "main/command_interface.py",
        "nrf_bridge_config.py": root / "main/configs/bridge_usb.py",
    }
    if options.master:
        copies.update({name: root / 'main' / name for name in
                       ('usb_master_control.py', 'usb_master_maintenance.py')})
    if options.compiled:
        if not options.master:
            parser.error('--compiled requires --master')
        from build_usb_master_bundle import build
        entry, compiled = build(root)
        # Include removed source destinations in the backup/rollback set.
        copies = {name: None for name in copies}
        copies['main.py'] = entry
        copies['usb_master_bundle.mpy'] = compiled
        copies['nrf_bridge_config.py'] = root / 'main/configs/bridge_usb.py'
    with serial.Serial(options.port, 115200, timeout=0.05, write_timeout=2) as port:
        raw.stop_running_program(port)
        raw.enter_raw_repl(port)
        touched = False
        try:
            for remote in copies:
                code = (
                    "import binascii\n"
                    "try:\n"
                    "    print(binascii.hexlify(open(%r, 'rb').read()).decode())\n"
                    "except OSError:\n"
                    "    print('MISSING')\n" % remote
                )
                output = raw.exec_raw(port, code).strip()
                if output != b"MISSING":
                    (backup / remote).write_bytes(bytes.fromhex(output.decode("ascii")))
            raw.exec_raw(port, "import select, sys\np=select.poll()\np.register(sys.stdin, select.POLLIN)\nprint(p.poll(0))\n")
            print("Original firmware saved to", backup)
            if options.master:
                original_config = backup / 'nrf_bridge_config.py'
                content = (original_config.read_bytes() if original_config.exists()
                           else copies['nrf_bridge_config.py'].read_bytes())
                prepared_config = backup / 'prepared_master_config.py'
                prepared_config.write_bytes(prepare_master_config(
                    content, (root / 'main/configs/bridge_usb.py').read_bytes()))
                copies['nrf_bridge_config.py'] = prepared_config
            touched = True
            # Reclaim all owned source destinations before writing a bundled image.
            # This touches only the files whose originals were just backed up.
            if options.compiled:
                raw.remove_remote_files(port, list(copies))
                # A serial install is already verified: drop any in-app update
                # trial so main.py does not "restore" an older bundle at boot.
                raw.remove_remote_files(port, ['update_pending', 'update_trial',
                                               'usb_master_bundle.mpy.bak'])
            for remote, local in copies.items():
                if local is None:
                    continue
                raw.exec_raw(port, "import os\ntry:\n    os.remove(%r)\nexcept OSError:\n    pass\n" % remote)
                raw.upload_file(port, str(local), remote)
                output = raw.exec_raw(
                    port, "import binascii\nprint(binascii.hexlify(open(%r, 'rb').read()).decode())\n" % remote
                )
                if bytes.fromhex(output.decode("ascii").strip()) != local.read_bytes():
                    raise RuntimeError("Upload verification failed: " + remote)
        except Exception:
            if touched:
                print("Installation failed; restoring saved files from", backup)
                raw.remove_remote_files(port, list(copies))
                for remote in copies:
                    saved = backup / remote
                    if saved.exists():
                        raw.upload_file(port, str(saved), remote)
            raise
        finally:
            raw.exit_raw_repl(port)
            port.write(raw.CTRL_D)
    print("USB master installed; live controls require an active host session."
          if options.master else "USB monitor installed; ARM/FIRE/script commands are rejected.")


if __name__ == "__main__":
    main()
