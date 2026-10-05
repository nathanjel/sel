"""SQL text literals are escaped by a translate table (single-character
rules) or one alternation regex (multi-character rules) instead of a Python loop.
Both must equal the loop they replaced, for the shipped dialects and for escape
tables with overlapping / multi-character keys."""
import random

import sel
from sel.sql import emit, map as _map


def ref_escape(escape, text):
    keys = sorted(escape.keys(), key=len, reverse=True)
    buf, i = [], 0
    while i < len(text):
        for k in keys:
            if k and text.startswith(k, i):
                buf.append(str(escape[k]))
                i += len(k)
                break
        else:
            buf.append(text[i])
            i += 1
    return ''.join(buf)


ALPHA = list("ab'\\\"%_\n\x00 é😀") + ['\\\\', "''"]


def test_shipped_dialects_match_the_loop():
    rnd = random.Random(20)
    for dialect in ('ansi', 'mysql', 'mariadb', 'postgresql', 'sqlite'):
        escape = _map.lexical(dialect, 'textEscape')
        quote = str(_map.lexical(dialect, 'textQuote'))
        for _ in range(500):
            t = ''.join(rnd.choice(ALPHA) for _ in range(rnd.randrange(0, 30)))
            want = quote + (ref_escape(escape, t) if isinstance(escape, dict) else t) + quote
            assert emit.text_literal(dialect, t) == want, (dialect, t)


def test_overlapping_and_multi_character_keys_match_the_loop():
    rnd = random.Random(21)
    tables = [
        {"'": "''", '\\': '\\\\'},
        {"'": "''", '\\\\': 'BB', '\\': 'B'},          # the longer rule must win
        {'ab': 'X', 'a': 'Y', 'abc': 'Z'},
        {'é': 'e', '😀': ':)'},
        {},
        {'': 'ignored', "'": "''"},
    ]
    for table in tables:
        fn = emit._escaper('test-dialect', table)
        for _ in range(500):
            t = ''.join(rnd.choice(list("abc'\\é😀 ")) for _ in range(rnd.randrange(0, 20)))
            assert fn(t) == ref_escape(table, t), (table, t)


def test_a_replaced_escape_table_is_not_served_from_the_cache():
    one = {"'": "''"}
    two = {"'": "\\'"}
    assert emit._escaper('cache-probe', one)("a'b") == "a''b"
    assert emit._escaper('cache-probe', two)("a'b") == "a\\'b"


def test_translation_is_unchanged_end_to_end():
    frag = sel.sql.Sql.translate(sel.compile('T $== "it\'s \\\\ fine"'), 'mariadb',
                                 {'T': sel.sql.Binding.column('t', type='TEXT')})
    assert "it''s" in frag.as_condition('inline') or "it\\'s" in frag.as_condition('inline')
