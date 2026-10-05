"""The runners' readers (python/bin/_harness.py): a file is bytes decoded as
UTF-8 with no newline translation, and a corpus record loses exactly one
trailing newline -- never a CR, never a second LF (tools/README.md)."""
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / 'python/bin'
if str(BIN) not in sys.path:
    sys.path.append(str(BIN))

import _harness  # noqa: E402


def write(tmp_path, data: bytes) -> str:
    p = tmp_path / 'corpus.selc'
    p.write_bytes(data)
    return str(p)


def test_read_text_keeps_every_cr(tmp_path):
    assert _harness.read_text(write(tmp_path, b'a\r\nb\rc\n')) == 'a\r\nb\rc\n'


def test_a_record_loses_exactly_one_newline_and_keeps_its_crs(tmp_path):
    text = _harness.read_text(write(tmp_path, b'### a\n1 +\r\n2\n### b\n(1\n\n'))
    assert _harness.read_corpus(text) == ['1 +\r\n2', '(1\n']
    text = _harness.read_text(write(tmp_path, b'### a\nX\r\n### b\n(1\n'))
    assert _harness.read_corpus(text) == ['X\r', '(1']


def test_the_conformance_reader_sees_the_suites_crs():
    conformance = runpy.run_path(str(BIN / 'conformance.py'))
    cases = conformance['parse_selt'](conformance['read_text'](str(ROOT / 'conformance/01-lexical.selt')),
                                      '01-lexical.selt')
    sources = {c['name']: c['source'] for c in cases}
    assert sources['lex.space.crlf-between-tokens'] == '1 ==\r\n1'
    assert sources['lex.space.lone-cr'] == '1 ==\r1'


def test_batch_gives_the_final_record_its_own_position(tmp_path):
    for data, want in ((b'### a\nLEN("a\r\nb")\n### b\n(1\n\n', ['t"4"', '!E_SYNTAX@2:1']),
                       (b'### a\nLEN("a\r\nb")\n### b\n(1\n', ['t"4"', '!E_SYNTAX@1:3'])):
        r = subprocess.run([sys.executable, str(BIN / 'batch.py'), write(tmp_path, data)],
                           capture_output=True, env={'PYTHONPATH': str(ROOT / 'python')})
        assert r.returncode == 0, r.stderr
        assert r.stdout.decode('utf-8').split('\n')[:-1] == want


# --- the runner contract: a bad path or an empty run is never a green run ------

def runner(*args):
    r = subprocess.run([sys.executable, *args], capture_output=True, cwd=ROOT,
                       env={'PYTHONPATH': str(ROOT / 'python')})
    return r.returncode, r.stdout.decode('utf-8'), r.stderr.decode('utf-8')


@pytest.mark.parametrize('script', ['conformance.py', 'batch.py', 'sqlfuzz', 'check-decimal.py'])
@pytest.mark.parametrize('path', ['no/such/file', 'conformance'])
def test_an_unreadable_path_is_one_line_and_a_failure(script, path):
    rc, _, errs = runner(str(BIN / script), path)
    assert rc != 0
    assert errs.startswith(f'cannot read {path}: ') and errs.count('\n') == 1, errs


@pytest.mark.parametrize('script', ['conformance.py', 'batch.py', 'sqlfuzz', 'check-decimal.py'])
def test_an_empty_input_is_a_failure(tmp_path, script):
    rc, _, errs = runner(str(BIN / script), write(tmp_path, b''))
    assert rc == 1 and errs.startswith('no '), errs


def test_a_sqlt_filter_that_matches_nothing_is_a_failure():
    rc, _, errs = runner(str(BIN / 'sqlt'), 'zzz-no-such-case')
    assert rc == 1 and errs == 'no case matched zzz-no-such-case\n'
