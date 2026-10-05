"""The binary codecs check their input here and compute with the stdlib.
The hand-written originals are kept below as the reference; the two must accept
exactly the same inputs and produce the same bytes/text."""
import random

import pytest
import sel
from sel.errors import SelError

B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
B64_INDEX = {ch: i for i, ch in enumerate(B64)}
HEX_OK = set('0123456789abcdefABCDEF')


class Bad(Exception):
    pass


def ref_encode(b):
    out = []
    for i in range(0, len(b), 3):
        n = (b[i] << 16) | ((b[i + 1] if i + 1 < len(b) else 0) << 8) | (b[i + 2] if i + 2 < len(b) else 0)
        out.append(B64[(n >> 18) & 63])
        out.append(B64[(n >> 12) & 63])
        out.append(B64[(n >> 6) & 63] if i + 1 < len(b) else '=')
        out.append(B64[n & 63] if i + 2 < len(b) else '=')
    return ''.join(out)


def ref_decode(s):
    if len(s) % 4 != 0:
        raise Bad
    out = bytearray()
    for i in range(0, len(s), 4):
        quad, padding = [], 0
        for k in range(4):
            ch = s[i + k]
            if ch == '=':
                if i + 4 < len(s) or k < 2:
                    raise Bad
                padding += 1
                quad.append(0)
                continue
            if padding > 0:
                raise Bad
            v = B64_INDEX.get(ch)
            if v is None:
                raise Bad
            quad.append(v)
        n = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3]
        out.append((n >> 16) & 255)
        if padding < 2:
            out.append((n >> 8) & 255)
        if padding < 1:
            out.append(n & 255)
    return bytes(out)


def ref_hex(s):
    if len(s) % 2:
        raise Bad
    out = bytearray(len(s) // 2)
    for i in range(len(out)):
        pair = s[i * 2:i * 2 + 2]
        if not all(c in HEX_OK for c in pair):
            raise Bad
        out[i] = int(pair, 16)
    return bytes(out)


def ref_crc(b):
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (0xEDB88320 ^ (c >> 1)) if (c & 1) else (c >> 1)
        table.append(c)
    crc = 0xFFFFFFFF
    for byte in b:
        crc = table[(crc ^ byte) & 255] ^ (crc >> 8)
    return f'{crc ^ 0xFFFFFFFF:08x}'


def host(fn, s):
    """(value) or ('E', code) for fn applied to the text s."""
    try:
        return sel.evaluate(f'TO_HEX({fn}(S))', {'S': s}).scalar
    except SelError as e:
        assert e.code == 'E_BAD_ARG', e.code
        return ('E', e.code)


def test_crc32_check_value_and_random_data():
    assert sel.evaluate('CRC32(FROM_HEX("313233343536373839"))').scalar == 'cbf43926'   # the catalogue check value of "123456789"
    rnd = random.Random(1)
    for n in (0, 1, 2, 3, 255, 256, 1000):
        data = bytes(rnd.randrange(256) for _ in range(n))
        got = sel.evaluate('CRC32(B)', {'B': sel.Value.bin(data)}).scalar
        assert got == ref_crc(data)


def test_encode_matches_the_reference():
    rnd = random.Random(2)
    for n in list(range(0, 40)) + [1000, 1001, 1002]:
        data = bytes(rnd.randrange(256) for _ in range(n))
        assert sel.evaluate('ENCODE_BASE64(B)', {'B': sel.Value.bin(data)}).scalar == ref_encode(data)


def test_decode_accepts_and_refuses_exactly_what_the_reference_does():
    rnd = random.Random(3)
    alphabet = list(B64[:6]) + list('+/=') + ['-', '_', ' ', '\n', 'é', '٣']
    cases = []
    for _ in range(6000):
        cases.append(''.join(rnd.choice(alphabet) for _ in range(rnd.randrange(0, 13))))
    # well-formed and nearly well-formed strings
    for n in range(0, 20):
        data = bytes(rnd.randrange(256) for _ in range(n))
        good = ref_encode(data)
        cases += [good, good[:-1], good + '=', good + 'A', good.replace('=', 'A'), '=' + good]
    cases += ['====', 'A===', 'AA==', 'AAA=', 'AA=A', 'AAAA====', 'QR==', 'QQ==', '', '    ']
    for s in cases:
        try:
            want = ref_decode(s).hex()
        except Bad:
            want = ('E', 'E_BAD_ARG')
        assert host('DECODE_BASE64', s) == want, repr(s)


def test_from_hex_accepts_and_refuses_exactly_what_the_reference_does():
    rnd = random.Random(4)
    alphabet = list('0123456789abcdefABCDEFgG') + [' ', '\n', '-', '٣', 'é', 'x']
    cases = [''.join(rnd.choice(alphabet) for _ in range(rnd.randrange(0, 11))) for _ in range(6000)]
    cases += ['', '00', 'ff', 'FF', 'fF', '0', '٣٣', '0 ', ' 0', '00 00', '0x', '12\n3']
    for s in cases:
        try:
            want = ref_hex(s).hex()
        except Bad:
            want = ('E', 'E_BAD_ARG')
        assert host('FROM_HEX', s) == want, repr(s)


def test_btl_ltb_round_trip_and_errors():
    data = bytes(range(256)) * 3
    v = sel.evaluate('BTL(B)', {'B': sel.Value.bin(data)})
    assert [int(x.scalar) for x in v.values()] == list(data)
    assert sel.evaluate('LTB(BTL(B))', {'B': sel.Value.bin(data)}).scalar == data
    # BTL elements are independent values (no shared mutable Value between bytes)
    r = sel.evaluate('L = BTL(B); L[1] = 99; L[2]', {'B': sel.Value.bin(bytes([5, 5, 5]))})
    assert r.scalar == '5'
    assert sel.evaluate('LTB(LIST(65, 1.0, "66", 67.00))').scalar == b'A\x01BC'
    for bad, code in (('LTB(LIST(256))', 'E_RANGE'), ('LTB(LIST(-1))', 'E_RANGE'),
                      ('LTB(LIST(1.5))', 'E_NOT_INT'), ('LTB(LIST("x"))', 'E_NOT_NUM')):
        with pytest.raises(SelError) as e:
            sel.evaluate(bad)
        assert e.value.code == code
