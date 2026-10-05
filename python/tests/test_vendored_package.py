"""The package keeps working when vendored under another name
(`myapp._vendor.sel`): nothing may decide "is this a shipped builtin?" from the
module a function lives in. LINK's run-time pre-filter needs its sources to be
pure, and a module-name test called every builtin impure under vendoring, so
the pre-filter silently never fired."""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PROBE = '''
import vend.sel as sel
from vend.sel.builtins.structure import _pure_source
from vend.sel.parser import may_write
ast = sel.compile('FILTER(LIST(1, 2), _ > 1)').ast
print(_pure_source(ast), may_write(ast), sel.evaluate('COUNT(FILTER(LIST(1, 2), _ > 1))').scalar)
'''


def test_a_vendored_copy_still_knows_its_builtins(tmp_path):
    shutil.copytree(ROOT / 'python/sel', tmp_path / 'vend/sel',
                    ignore=shutil.ignore_patterns('__pycache__'))
    (tmp_path / 'vend/__init__.py').write_text('')
    r = subprocess.run([sys.executable, '-c', PROBE], cwd=tmp_path, capture_output=True,
                       env={'PYTHONPATH': str(tmp_path)}, timeout=120)
    assert r.returncode == 0, r.stderr.decode()
    assert r.stdout.decode().split() == ['True', 'False', '1']
