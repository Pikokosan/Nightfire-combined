"""Bundle the USB master into one mpy file for the nice!nano's 64 KiB FS."""
import ast
from pathlib import Path
import subprocess
import sys


INTERNAL = {'bridge_radio', 'usb_bridge_transport', 'usb_monitor_nfcp',
            'command_interface', 'usb_master_control', 'usb_master_maintenance'}
SOURCES = ('bridge_radio.py', 'usb_bridge_transport.py', 'usb_monitor_nfcp.py',
           'command_interface.py', 'usb_master_control.py',
           'usb_master_maintenance.py', 'nrf_usb_monitor_main.py')


class RemoveInternalImports(ast.NodeTransformer):
    def visit_Import(self, node):
        node.names = [name for name in node.names if name.name not in INTERNAL]
        return node if node.names else None

    def visit_ImportFrom(self, node):
        return None if node.module in INTERNAL else node


def bundle_source(source_root):
    statements = []
    for filename in SOURCES:
        tree = ast.parse((source_root / filename).read_text(encoding='utf-8'))
        tree = RemoveInternalImports().visit(tree)
        statements.extend(tree.body)
        if filename == 'bridge_radio.py':
            # Preserve the module-qualified API, and the original function globals.
            # A class namespace needs no module allocation or runtime exec().
            aliases = []
            for node in tree.body:
                if isinstance(node, ast.FunctionDef):
                    aliases.append('    %s = staticmethod(%s)' % (node.name, node.name))
                elif isinstance(node, ast.ClassDef):
                    aliases.append('    %s = %s' % (node.name, node.name))
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            aliases.append('    %s = %s' % (target.id, target.id))
            namespace = 'class _BridgeRadio:\n' + '\n'.join(aliases) + '\nbridge_radio = _BridgeRadio\n'
            statements.extend(ast.parse(namespace).body)
    return ast.unparse(ast.fix_missing_locations(ast.Module(body=statements, type_ignores=[]))) + '\n'


def build(root, output=None):
    output = output or root / 'dist/android/usb-master-bundle'
    output.mkdir(parents=True, exist_ok=True)
    source = output / 'usb_master_bundle.py'
    source.write_text(bundle_source(root / 'main'), encoding='utf-8')
    compiled = output / 'usb_master_bundle.mpy'
    command = [sys.executable, '-m', 'mpy_cross', '-O2', '-o', str(compiled),
               '-s', 'usb_master_bundle.py', str(source)]
    try:
        subprocess.run(command, check=True)
    except (OSError, subprocess.CalledProcessError):
        # The Windows wheel is unavailable on some hosts; use the same WSL
        # compiler used for the compact field image.
        def wsl_path(path):
            value = str(Path(path).resolve()).replace('\\', '/')
            return '/mnt/' + value[0].lower() + value[2:]
        wsl_home = subprocess.check_output(
            ['wsl.exe', '--', 'sh', '-lc', 'printf %s ~'], text=True
        ).strip()
        subprocess.run([
            'wsl.exe', '--', wsl_home + '/.local/bin/mpy-cross',
            '-O2', '-o', wsl_path(compiled), '-s',
            'usb_master_bundle.py', wsl_path(source),
        ], check=True)
    entry = output / 'main.py'
    entry.write_text('import usb_master_bundle\n', encoding='ascii')
    print('Compiled master bundle:', compiled, compiled.stat().st_size, 'bytes')
    return entry, compiled


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[1])
