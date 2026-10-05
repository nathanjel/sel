"""SPEC 2: invalid source is E_UTF8 at the first invalid unit, counted in code
points; a host reads source as bytes, unchanged."""
import os
import subprocess
import sys

import pytest
import sel
from sel import SelError
from sel.utf8 import decode_source

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def err(source):
    with pytest.raises(SelError) as info:
        sel.compile(source)
    e = info.value
    return e.code, e.line, e.col, e.offset


@pytest.mark.parametrize('source,want', [
    ('\ud800', (1, 1, 0)),
    ('1 + \ud800', (1, 5, 4)),
    ('1 +\n \ud800', (2, 2, 5)),
    ('"\U0001F600" + \udc00', (1, 7, 6)),          # an astral pair is ONE code point
    ('"łł\ud800"', (1, 4, 3)),
])
def test_lone_surrogate_is_positioned(source, want):
    assert err(source) == ('E_UTF8',) + want


def test_a_valid_astral_pair_is_not_an_error():
    assert sel.evaluate('LEN("\U0001F600")').scalar == '1'


@pytest.mark.parametrize('data,want', [
    (b'\xff', (1, 1, 0)),
    (b'"a\xffb"', (1, 3, 2)),
    (b'1 +\n "a\xffb"', (2, 4, 7)),
    (b'"\xc5\x82\xff"', (1, 3, 2)),
    (b'"\xe2\x82', (1, 2, 1)),                     # truncated at end
    (b'"\xc0\x80"', (1, 2, 1)),                    # overlong
    (b'"\xed\xa0\x80"', (1, 2, 1)),                # encoded surrogate
    (b'"\xf4\x90\x80\x80"', (1, 2, 1)),            # above U+10FFFF
    (b'\r\r"\xff', (1, 4, 3)),                     # CR is not a line end
])
def test_decode_source_positions(data, want):
    with pytest.raises(SelError) as info:
        decode_source(data)
    e = info.value
    assert (e.code, e.line, e.col, e.offset) == ('E_UTF8',) + want


def test_decode_source_keeps_newlines_untouched():
    assert decode_source(b'a\r\nb\rc') == 'a\r\nb\rc'


def cli(*args, stdin=None):
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'python'))
    r = subprocess.run([sys.executable, '-m', 'sel', *args], input=stdin,
                       capture_output=True, env=env, timeout=60)
    return r.returncode, r.stdout.decode(), r.stderr.decode()


def write(tmp_path, data):
    p = tmp_path / 'x.sel'
    p.write_bytes(data)
    return str(p)


def test_cli_crlf_inside_a_literal_stays_two_characters(tmp_path):
    rc, out, _ = cli(write(tmp_path, b'A = "a\r\nb";\nLEN(A)'))
    assert (rc, out.strip()) == (0, '4')


def test_cli_cr_is_not_a_line_end(tmp_path):
    rc, _, errs = cli(write(tmp_path, b'A = 1 # c\r+ 2\r\nA'))
    assert rc == 1 and errs.startswith('E_SYNTAX at line 2 column 1')


def test_cli_invalid_utf8_file_is_e_utf8_not_a_traceback(tmp_path):
    rc, _, errs = cli(write(tmp_path, b'1 +\n "a\xffb"'))
    assert rc == 1
    assert errs.startswith('E_UTF8 at line 2 column 4')
    assert 'Traceback' not in errs


def test_cli_invalid_utf8_in_dash_e_is_positioned():
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, 'python'))
    r = subprocess.run([sys.executable, '-m', 'sel', '-e', b'1 + "a\xffb"'],
                       capture_output=True, env=env, timeout=60)
    assert r.returncode == 1
    assert r.stderr.decode().startswith('E_UTF8 at line 1 column 7')


def test_cli_repl_reads_bytes_and_survives_an_invalid_line():
    rc, out, errs = cli(stdin=b'A = "a\rb"\nLEN(A)\n"x\xffy"\n1 + 1\n')
    assert rc == 0
    assert '3' in out and '2' in out
    assert errs.startswith('E_UTF8 at line 1 column 3')


# --- the CLI contract every host follows (docs/usage/repl.md) ----------------

@pytest.mark.parametrize('args, message', [
    (['-e'], 'sel: -e needs an expression'),
    (['--deps', '-e'], 'sel: -e needs an expression'),
    (['--nope'], 'sel: unknown option --nope'),
    (['-x', '-e', '1'], 'sel: unknown option -x'),
    (['-e', '1', 'extra'], 'sel: unexpected argument extra'),
    (['a.sel', 'b.sel'], 'sel: unexpected argument b.sel'),
    (['a.sel', '-e', '1'], 'sel: unexpected argument 1'),
])
def test_cli_usage_errors_exit_2_with_a_sel_prefix(args, message):
    rc, out, errs = cli(*args)
    assert (rc, out) == (2, '')
    assert errs.startswith(message)


@pytest.mark.parametrize('flag', ['--help', '-h'])
def test_cli_help_is_usage_on_stdout(flag):
    rc, out, errs = cli(flag)
    assert (rc, errs) == (0, '')
    assert out.startswith('usage: sel')


def test_cli_version():
    assert cli('--version') == (0, f'sel {sel.__version__}\n', '')


@pytest.mark.parametrize('kind', ['missing', 'directory'])
def test_cli_an_unreadable_file_exits_1(tmp_path, kind):
    path = str(tmp_path / 'nope.sel') if kind == 'missing' else str(tmp_path)
    rc, out, errs = cli(path)
    assert (rc, out) == (1, '')
    assert errs.startswith(f'sel: cannot read {path}')
    assert 'Traceback' not in errs


def test_cli_an_evaluation_error_exits_1():
    assert cli('-e', '1 +') == (1, '', 'E_SYNTAX at line 1 column 4: unexpected end of input\n')


def test_cli_an_empty_dependency_list_prints_nothing():
    assert cli('--deps', '-e', '1') == (0, '', '')
    assert cli('-e', 'B + A', '--deps') == (0, 'A\nB\n', '')


def test_cli_dash_e_takes_the_next_argument_whatever_it_looks_like():
    assert cli('-e', '-1') == (0, '-1\n', '')


def test_cli_repl_on_a_pipe_writes_no_prompt():
    assert cli(stdin=b'A = 1\nA + 1\n') == (0, '1\n2\n', '')


def test_cli_repl_skips_only_sel_whitespace_lines():
    rc, out, errs = cli(stdin=b' \t\r\n\n1 + 1\n\xc2\xa0\n\x0b\n2\n')
    assert (rc, out) == (0, '2\n2\n')
    assert errs.count('E_SYNTAX at line 1 column 1') == 2
