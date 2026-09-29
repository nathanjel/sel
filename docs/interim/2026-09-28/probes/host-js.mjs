import { compile, Value } from '../wt2/js/src/sel.mjs'; const Sel = { compile };
const T = (name, f) => { let r; try { r = f(); r = 'OK ' + (typeof r === 'string' ? r : r instanceof Value ? r.dump().slice(0, 80) : JSON.stringify(r)?.slice(0, 80)); } catch (e) { r = 'ERR ' + (e.code || e.constructor.name) + ' ' + String(e.message).slice(0, 70); } console.log(name.padEnd(34), r); };
const big = 10n ** 1000000n; // 1,000,001 digits
T('int(10^1e6) [control]', () => Value.int(big));
T('fromNative(10^1e6)', () => Value.fromNative(big));
T('fromNative({a:10^1e6})', () => Value.fromNative({ a: big }));
T('run ctx X=10^1e6 → X+0', () => Sel.compile('X + 0').run({ X: big }));
T('run ctx X=10^1e6 → LEN(X)', () => Sel.compile('LEN(X)').run({ X: big }));
T('num("1"*1000001) string', () => Value.num('1'.repeat(1000001)));
T('num("0."+"1"*1000001) string', () => Value.num('0.' + '1'.repeat(1000001)));
T('fromNative(1e308 number)', () => Value.fromNative(1e308).dump().length);
T('fromNative(5e-324 number)', () => Value.fromNative(5e-324).dump().length);
const lone = 'a\uD800';
T('text(lone) [control]', () => Value.text(lone));
T('fromNative({lone:1}) [control]', () => Value.fromNative({ [lone]: 1 }));
T('none().set(lone) [control]', () => Value.none().set(lone, Value.text('1')));
T('shaped([lone],[1])', () => Value.shaped([lone], [Value.text('1')]));
T('fromEntries([[lone,1]])', () => Value.fromEntries([[lone, Value.text('1')]]));
T('shaped key count < values', () => Value.shaped(['a'], [Value.text('1'), Value.text('2')]));
T('shaped key count > values', () => Value.shaped(['a', 'b'], [Value.text('1')]));
T('shaped duplicate keys', () => Value.shaped(['a', 'a'], [Value.text('1'), Value.text('2')]));
T('fromEntries duplicate keys', () => Value.fromEntries([['a', Value.text('1')], ['a', Value.text('2')]]));
T('bin([true])', () => Value.bin([true]));
T('bin([1.5])', () => Value.bin([1.5]));
T('bin(["1"])', () => Value.bin(['1']));
// aliasing: keys array retained by shape?
{ const keys = ['k1', 'k2']; const v = Value.shaped(keys, [Value.text('1'), Value.text('2')]); keys[0] = 'zz'; T('shaped: mutate caller keys after', () => v.dump()); const w = Value.shaped(['k1', 'k2'], [Value.text('3'), Value.text('4')]); T('  …fresh shaped same keys', () => w.dump()); }
{ const vals = [Value.text('1')]; const v = Value.list(vals); vals[0] = Value.text('X'); T('list: mutate caller array after', () => v.dump()); }
{ const vals = [Value.text('1')]; const v = Value.shaped(['a'], vals); vals[0] = Value.text('X'); T('shaped: mutate caller values after', () => v.dump()); }
{ const inner = Value.text('1'); const v = Value.list([inner]); T('list: element Value aliased (same obj)?', () => String(v.get('1') === inner)); }
{ const ent = [['a', Value.text('1')]]; const v = Value.fromEntries(ent); ent[0][1] = Value.text('X'); ent.push(['b', Value.text('2')]); T('fromEntries: mutate caller after', () => v.dump()); }
{ const u = new Uint8Array([1, 2]); const v = Value.fromNative({ b: u }); u[0] = 9; T('fromNative Uint8Array copied', () => v.dump()); }
{ const v = Value.bin([1, 2]); const n = v.toNative(); n[0] = 9; T('toNative BIN copied', () => v.dump()); }
{ const v = Value.fromNative({ a: 'x' }); const n = v.toNative(); n.a = 'y'; T('toNative map copied', () => v.dump()); }
