"""tools/check.sh names every lane a caller opted out of, at the top and in its
last line, so an opted-out run cannot pass for the gate. The test runs the
SKIPPED block and the two prints that use it, extracted from the script, rather
than the gate itself."""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, '..', '..', 'tools', 'check.sh')

# python/tests ships in the sdist without tools/.
if not os.path.isfile(CHECK) or shutil.which('bash') is None:
    pytest.skip('tools/check.sh or bash is not available', allow_module_level=True)

OLD = ['SEL_SKIP_SANITIZERS', 'SEL_SKIP_SQL_BUDGETS', 'SEL_SKIP_DECIMAL_MUTATIONS',
       'SEL_SKIP_DB_TESTS', 'SEL_SKIP_PYTHON_UNIT', 'SEL_SKIP_PHP_PACKAGE']
NEW = ['SEL_SKIP_PHP81', 'SEL_SKIP_CPP_PACKAGE', 'SEL_SKIP_GO_MODULE',
       'SEL_SKIP_RUST_PACKAGE']


def _lines():
    with open(CHECK, encoding='utf-8') as f:
        return f.read().split('\n')


def _index(lines, pred, start=0, what=''):
    for i in range(start, len(lines)):
        if pred(lines[i]):
            return i
    raise AssertionError('tools/check.sh: no line ' + what)


def _snippet():
    lines = _lines()
    a = _index(lines, lambda l: l == 'SKIPPED=""', what='SKIPPED=""')
    b = _index(lines, lambda l: l == 'SKIPPED="${SKIPPED# }"', a, 'SKIPPED="${SKIPPED# }"')
    echo = _index(lines, lambda l: l.startswith('[ -z "$SKIPPED" ] || echo "opted out:'),
                  what='echoing "opted out:"')
    c = _index(lines, lambda l: l.startswith('if [ "$status" -eq 0 ] && [ -z "$NOT_RUN" ]'),
               what='with the final if')
    d = _index(lines, lambda l: l == 'fi', c, 'closing the final if')
    return '\n'.join(lines[a:b + 1] + [lines[echo]] + lines[c:d + 1]) + '\n'


def _run(**switches):
    script = "IMPLS='js php'; NOT_RUN=''; status=0\n" + _snippet()
    proc = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                          env={'PATH': os.environ['PATH'], **switches}, check=True)
    assert proc.stderr == ''
    return proc.stdout


def _comment_above():
    lines = _lines()
    i = _index(lines, lambda l: l.startswith('[ "${SEL_SKIP_DB_TESTS:-0}" = 1 ]'),
               what='opting out of SEL_SKIP_DB_TESTS')
    j = i
    while j > 0 and lines[j - 1].startswith('#'):
        j -= 1
    return ' '.join(l.lstrip('#').strip() for l in lines[j:i])


def _expected(names):
    skipped = ' '.join(names)
    return 'opted out: %s\nGREEN, PARTIAL — js php; opted out: %s\n' % (skipped, skipped)


@pytest.mark.parametrize('name', NEW)
def test_new_switch_is_named(name):
    assert _run(**{name: '1'}) == _expected([name])


def test_every_switch_in_list_order():
    assert _run(**{n: '1' for n in OLD + NEW}) == _expected(OLD + NEW)


def test_none_set_is_all_green():
    assert _run() == 'ALL GREEN — js php\n'


@pytest.mark.parametrize('name', OLD)
def test_existing_switch_unchanged(name):
    assert _run(**{name: '1'}) == _expected([name])


def test_switch_not_1_is_ignored():
    assert _run(SEL_SKIP_PHP81='0', SEL_SKIP_GO_MODULE='') == 'ALL GREEN — js php\n'


def test_comment_names_every_lane():
    comment = _comment_above()
    for words in ['PHP 8.1', 'C++ package', 'Go module', 'Rust package',
                  'check-php-version.sh', 'check-cpp-package.sh', 'check-go-module.sh',
                  'check-rust-package.sh', 'database', 'Python unit', 'PHP package']:
        assert words in comment, words
