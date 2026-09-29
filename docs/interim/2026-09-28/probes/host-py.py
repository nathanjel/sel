import sys; sys.path.insert(0, sys.argv[1]); sys.set_int_max_str_digits(0) if len(sys.argv) > 2 else None
import sel
from sel import Value, compile as C
from sel import decimal as D
def T(n, f):
    try:
        r = f(); o = 'OK ' + (r.dump()[:80] if isinstance(r, Value) else repr(r)[:80])
    except Exception as e:
        o = 'ERR ' + str(getattr(e, 'code', type(e).__name__)) + ' ' + str(e)[:70]
    print(f'{n:<38} {o}'.encode('utf-8', 'backslashreplace').decode())
lone = 'a\ud800'
T('text(lone) [control]', lambda: Value.text(lone))
T('from_native({lone:1}) [control]', lambda: Value.from_native({lone: 1}))
T('none().set(lone) [control]', lambda: Value.none().set(lone, Value.text('1')))
T('shaped([lone],[1])', lambda: Value.shaped([lone], [Value.text('1')]))
T('record([lone],[1])', lambda: Value.record([lone], [Value.text('1')]))
T('record([lone],[1]) → INDEXES', lambda: C('INDEXES(X)').run({'X': Value.record([lone], [Value.text('1')])}))
T('from_entries([(lone,1)])', lambda: Value.from_entries([(lone, Value.text('1'))]))
T('list([1],[lone]) keys', lambda: Value.list([Value.text('1')], [lone]))
T('record fewer values', lambda: Value.record(['a', 'b'], [Value.text('1')]).dump())
T('record fewer values → run X["b"]', lambda: C('X["b"]').run({'X': Value.record(['a', 'b'], [Value.text('1')])}))
T('record extra values', lambda: Value.record(['a'], [Value.text('1'), Value.text('2')]))
T('record extra values size()', lambda: Value.record(['a'], [Value.text('1'), Value.text('2')]).size())
T('shaped duplicate keys', lambda: Value.shaped(['a', 'a'], [Value.text('1'), Value.text('2')]))
T('record duplicate keys', lambda: Value.record(['a', 'a'], [Value.text('1'), Value.text('2')]))
T('list keys count mismatch', lambda: Value.list([Value.text('1')], ['1', '2']))
T('list duplicate keys', lambda: Value.list([Value.text('1'), Value.text('2')], ['5', '5']))
T('bin([True])', lambda: Value.bin([True]))
T('bin([True,False])', lambda: Value.bin([True, False]))
T('from_native bytes [control]', lambda: Value.from_native(b'\x01'))
T('bin([1.0])', lambda: Value.bin([1.0]))
T('bin(bytearray) copy?', lambda: 'n/a')
T('num(Dec scale 1000001)', lambda: Value.num(D.Dec(False, '1', 1000001)) if hasattr(D, 'Dec') else 'no Dec')
T('int(10**1000000) [control]', lambda: Value.int(10**1000000))
T('from_native(10**1000000)', lambda: Value.from_native(10**1000000))
T('run ctx X=10**1000000', lambda: C('X').run({'X': 10**1000000}))
T('from_native(10**5000) (>4300 str limit)', lambda: len(Value.from_native(10**5000).dump()))
T('run ctx 10**5000 → X+1', lambda: len(C('X + 1').run({'X': 10**5000}).dump()))
T('num("1"*1000001)', lambda: Value.num('1' * 1000001))
# aliasing
ks = ['k1', 'k2']; vs = [Value.text('1'), Value.text('2')]; v = Value.record(ks, vs); vs[0] = Value.text('X'); T('record: mutate caller values', lambda: v)
ks = ['k1', 'k2']; vs = [Value.text('1'), Value.text('2')]; v = Value.shaped(ks, vs); vs[0] = Value.text('X'); ks[0] = 'zz'; T('shaped: mutate caller values+keys', lambda: v)
vs = [Value.text('1')]; v = Value.list(vs); vs[0] = Value.text('X'); vs.append(Value.text('Y')); T('list: mutate caller list', lambda: v)
ks = ['2']; vs = [Value.text('1')]; v = Value.list(vs, ks); ks[0] = '9'; T('list: mutate caller keys', lambda: v)
ent = [('a', Value.text('1'))]; v = Value.from_entries(ent); ent.append(('b', Value.text('2'))); T('from_entries: mutate caller', lambda: v)
ba = bytearray(b'\x01'); v = Value.bin(ba); ba[0] = 9; T('bin(bytearray) mutate', lambda: v)
v = Value.from_native({'a': ['x']}); n = v.to_native(); n['a'].append('y'); T('to_native mutate', lambda: v)
