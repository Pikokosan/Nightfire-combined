"""Bundle the USB master into one mpy file for the nice!nano's 64 KiB FS."""
import ast
from pathlib import Path
import subprocess
import sys


INTERNAL = {'bridge_radio', 'mesh', 'usb_bridge_transport', 'usb_monitor_nfcp',
            'command_interface', 'usb_master_control', 'usb_master_maintenance'}
SOURCES = ('bridge_radio.py', 'mesh.py', 'usb_bridge_transport.py', 'usb_monitor_nfcp.py',
           'command_interface.py', 'usb_master_control.py',
           'usb_master_maintenance.py', 'nrf_usb_monitor_main.py')

# Boot entry. A bundle installed over USB (usb_master_maintenance) is marked
# update_pending. Its first boot turns that into update_trial; the bundle
# deletes the marker when the app opens a host session. A trial that fails to
# import, or that is still unconfirmed at the next boot, is replaced by the
# previous bundle (.bak). The failed copy is deleted, so this cannot ping-pong.
ENTRY = """import os


def _exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False


def _rollback():
    if not _exists('usb_master_bundle.mpy.bak'):
        return False
    try:
        os.remove('usb_master_bundle.mpy')
    except OSError:
        pass
    os.rename('usb_master_bundle.mpy.bak', 'usb_master_bundle.mpy')
    for marker in ('update_trial', 'update_pending'):
        try:
            os.remove(marker)
        except OSError:
            pass
    return True


if _exists('update_trial'):
    print('Bridge update was not confirmed by the app; restoring previous bundle')
    _rollback()
elif _exists('update_pending'):
    os.rename('update_pending', 'update_trial')
try:
    import usb_master_bundle
except Exception as error:
    print('Bridge bundle failed to start:', error)
    if _exists('update_trial') and _rollback():
        import machine
        machine.reset()
    raise
"""


# Bundled modules that the rest of the code uses through their module name.
NAMESPACED = {
    'bridge_radio.py': ('_BridgeRadio', 'bridge_radio'),
    'mesh.py': ('_Mesh', 'mesh'),
}


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
        if filename in NAMESPACED:
            # Preserve the module-qualified API (bridge_radio.x, mesh.x), and
            # the original function globals. A class namespace needs no module
            # allocation or runtime exec().
            class_name, module_name = NAMESPACED[filename]
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
            namespace = 'class %s:\n' % class_name + '\n'.join(aliases) + '\n%s = %s\n' % (module_name, class_name)
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
    entry.write_text(ENTRY, encoding='ascii')
    print('Compiled master bundle:', compiled, compiled.stat().st_size, 'bytes')
    return entry, compiled


if __name__ == '__main__':
    build(Path(__file__).resolve().parents[2])
