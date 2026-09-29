import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { compile, Value, RecordShape } from '../js/src/sel.mjs';
import { parse } from '../js/src/parser.mjs';
import { evalNode, Context } from '../js/src/eval.mjs';
import { optimizeAstLogical, unwindPipeline } from '../js/src/optimizer.mjs';
import { decodeSource } from '../js/src/utf8.mjs';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

let checks = 0;
// Each import environment starts in a separate process: module caching must
// not hide an import-time write. Include SQL and both published bundles.
const entries = process.argv.length > 2 ? process.argv.slice(2)
  : ['js/src/sel.mjs', 'js/src/sql/index.mjs'];
for (const entry of entries) {
  for (const mode of ['absent', 'frozen', 'custom']) {
    const code = `
      import assert from 'node:assert/strict';
      if (${JSON.stringify(mode)} === 'custom') {
        Object.defineProperty(BigInt.prototype, 'toJSON', {
          value() { return 'host-owned'; }, configurable: true
        });
      }
      if (${JSON.stringify(mode)} === 'frozen') Object.freeze(BigInt.prototype);
      const expected = Object.getOwnPropertyDescriptors(BigInt.prototype);
      const sel = await import(${JSON.stringify(pathToFileURL(resolve(entry)).href)});
      const actual = Object.getOwnPropertyDescriptors(BigInt.prototype);
      assert.deepEqual(Reflect.ownKeys(actual), Reflect.ownKeys(expected));
      for (const key of Reflect.ownKeys(expected)) {
        assert.deepEqual(Reflect.ownKeys(actual[key]), Reflect.ownKeys(expected[key]));
        for (const field of Reflect.ownKeys(expected[key])) {
          assert.equal(actual[key][field], expected[key][field]);
        }
      }
      if (${JSON.stringify(mode)} === 'custom') assert.equal(JSON.stringify(1n), '"host-owned"');
      else assert.throws(() => JSON.stringify(1n), TypeError);
      if (sel.compile) assert.equal(sel.compile('1+2').run().asText(), '3');
    `;
    const result = spawnSync(process.execPath, ['--input-type=module', '-e', code], { encoding: 'utf8' });
    assert.equal(result.status, 0, `${entry}/${mode}: ${result.stderr}`);
    checks++;
  }
}

function outcome(source, input, layout) {
  const ast = parse(source);
  if (layout === 'generic') ast.recordShape = null;
  if (layout === 'stale') ast.recordShape = new RecordShape(['wrong', 'layout']);
  const root = Value.fromNative(input);
  try {
    const value = evalNode(ast, new Context(root));
    return { output: value.dump(), context: root.dump() };
  } catch (error) {
    return { error: [error.code, error.line, error.col, error.message], context: root.dump() };
  }
}
const cases = [
  ['RECORD("a", (X=1), "b", (X+=1))', {}],
  ['RECORD("before", X, "after", (X["v"]=2; X))', { X: { v: 1 } }],
  ['RECORD("a", (X=1), "b", (X=2; 1/0), "c", (X=3))', {}],
  ['RECORD("a", X, "b", MISSING)', { X: '2.50' }],
  ['RECORD(K, (X+=1), (K="second"), (X+=1))', { K: 'first', X: 0 }],
  ['RECORD("same", (X+=1), "same", (X+=1))', { X: 0 }],
  ['RECORD()', {}],
  ['RECORD("0", X, "__proto__", Y, "", Z)', { X: '01', Y: '2.50', Z: null }],
  ['RECORD(K, (X=1), "b", (X=2))', { K: false, X: 0 }],
];
for (const [source, input] of cases) {
  const expected = outcome(source, input, 'generic');
  for (const layout of ['prepared', 'stale']) {
    assert.deepEqual(outcome(source, input, layout), expected, `${layout}: ${source}`);
    checks++;
  }
}
const root = Value.fromNative({ X: { v: 1 } });
const program = compile('RECORD("x", X)');
const first = program.run(root), second = program.run(root);
assert.equal(first.shape, program.physicalAst().recordShape);
assert.equal(second.shape, first.shape);
first.get('x').set('v', Value.int(9));
assert.equal(second.get('x').get('v').asText(), '1');
assert.equal(root.get('X').get('v').asText(), '1');
checks++;

// --- the host boundary (spec/SPEC.md §8, review 2026-09-25 HOST-01..10) ------
// Collected rather than asserted one by one, so a run reports every broken
// contract at once.
const boundary = [];
const expectOk = (name, fn) => {
  checks++;
  try { fn(); } catch (e) { boundary.push(`${name}: ${String(e && e.message).split("\n")[0]}`); }
};
const expectCode = (name, code, fn) => expectOk(name, () => {
  let got = null;
  try { fn(); } catch (e) { got = e.code || String(e); }
  assert.equal(got, code, `expected ${code}, got ${got === null ? 'no error' : got}`);
});
const deep = (levels) => {
  let v = Value.text('x');
  for (let i = 0; i < levels; i++) v = Value.list([v]);
  return Value.list([v]);
};
// HOST-01: every key survives toNative as an own property, __proto__ included.
expectOk('toNative keeps an own __proto__ key', () => {
  const n = Value.fromNative(JSON.parse('{"__proto__": {"x": "1"}, "a": "2"}')).toNative();
  assert.equal(Object.hasOwn(n, '__proto__'), true);
  assert.equal(Object.getPrototypeOf(n), Object.prototype);
  assert.equal(n.x, undefined);
  assert.deepEqual(Object.keys(n), ['__proto__', 'a']);
  assert.equal(Value.fromNative(n).dump(), '-{"__proto__"=-{"x"=t"1"}, "a"=t"2"}');
});
expectOk('toNative keeps a scalar __proto__ nested in a list', () => {
  const n = Value.fromNative([JSON.parse('{"__proto__": "5"}')]).toNative();
  assert.equal(Object.hasOwn(n['1'], '__proto__'), true);
  assert.equal(n['1'].__proto__ === '5' || Object.getOwnPropertyDescriptor(n['1'], '__proto__').value === '5', true);
});
expectCode('toNative refuses a scalar with a child named _', 'E_BAD_ARG',
  () => compile('A = "s"; A["_"] = "c"; A').run().toNative());
// HOST-02 / HOST-03: the boundary copies, both ways.
expectOk('Value.bin copies the caller\'s bytes', () => {
  const b = new Uint8Array([1]); const v = Value.bin(b); b[0] = 2;
  assert.equal(v.dump(), 'b01');
});
expectOk('fromNative copies the caller\'s bytes', () => {
  const b = new Uint8Array([1]); const v = Value.fromNative({ k: b }); b[0] = 2;
  assert.equal(v.dump(), '-{"k"=b01}');
});
expectOk('toNative returns bytes the host owns', () => {
  const v = Value.fromNative({ k: new Uint8Array([1]) }); v.toNative().k[0] = 9;
  assert.equal(v.dump(), '-{"k"=b01}');
});
// HOST-04: bytes are whole numbers 0..255.
expectOk('Value.bin accepts 0 and 255', () => assert.equal(Value.bin([0, 255]).dump(), 'b00ff'));
for (const bad of [256, -1, 1.5, NaN, '1']) {
  expectCode(`Value.bin rejects ${JSON.stringify(bad)}`, 'E_RANGE', () => Value.bin([bad]));
}
// HOST-05: every text entering is checked, keys included.
expectCode('Value.text rejects a lone surrogate', 'E_UTF8', () => Value.text('\uD800'));
expectCode('fromNative rejects a lone surrogate', 'E_UTF8', () => Value.fromNative('\uD800'));
expectCode('fromNative rejects a lone-surrogate key', 'E_UTF8', () => Value.fromNative({ ['\uD800']: 'x' }));
expectCode('Value.set rejects a lone-surrogate key', 'E_UTF8', () => Value.none().set('\uDC00', Value.text('x')));
expectOk('a supplementary character is text', () => assert.equal(Value.text('\u{1F600}').dump(), 't"\u{1F600}"'));
// HOST-06: the digit caps hold for native integers (the boundary itself, both sides).
expectOk('Value.int of 1,000,000 digits is a number', () => {
  assert.equal(compile('LEN(A) == 1000000').run({ A: Value.int(10n ** 999999n) }).dump(), 'TRUE');
});
expectCode('Value.int of 1,000,001 digits is E_RANGE', 'E_RANGE', () => Value.int(10n ** 1000000n));
// HOST-07: an over-deep host value cannot be hashed any more than dumped.
for (const src of ['COUNT(DEDUPE(A))', 'COUNT(DISTINCT(A))', 'COUNT(BUCKET(A, _, COUNT(_)))']) {
  expectCode(`${src} over a value nested past the cap`, 'E_DEPTH', () => compile(src).run({ A: deep(250) }));
  expectOk(`${src} just below the cap`, () => assert.equal(compile(src).run({ A: deep(198) }).dump(), 't"1"'));
}
// HOST-08 / HOST-09: toNative and fromNative are inverses.
for (const src of ['FILTER(LIST(1,2,3), _ > 1)', 'RECORD("0","a","1","b")', 'FALSE', 'RECORD("a", FALSE)', 'LIST(TRUE, NULL)']) {
  expectOk(`round trip of ${src}`, () => {
    const v = compile(src).run();
    assert.equal(Value.fromNative(v.toNative()).dump(), v.dump());
  });
}
// HOST-10: a compiled program keeps nothing from one run to the next.
expectOk('a compiled program reads the key of each run', () => {
  const p = compile('A[K]'); const A = Value.fromNative({ x: '1', y: '2' });
  assert.equal(p.run({ A, K: 'x' }).dump() + p.run({ A, K: 'y' }).dump(), 't"1"t"2"');
});
// --- every public constructor (spec/SPEC.md §8, review 2026-09-28 HOST-11..20) --
const one = Value.text('1'); const two = Value.text('2');
// HOST-11: fromNative and a run context hold the integer digit cap too.
expectCode('fromNative of a 1,000,001-digit bigint is E_RANGE', 'E_RANGE', () => Value.fromNative(10n ** 1000000n));
expectCode('a run context holding one is E_RANGE', 'E_RANGE', () => compile('LEN(X)').run({ X: 10n ** 1000000n }));
// HOST-12: keys given side by side are checked like any other text.
expectCode('shaped rejects a lone-surrogate key', 'E_UTF8', () => Value.shaped(['a\uD800'], [one]));
expectCode('fromEntries rejects a lone-surrogate key', 'E_UTF8', () => Value.fromEntries([['a\uD800', one]]));
expectCode('fromEntries rejects one in a list key', 'E_UTF8', () => Value.fromEntries([['\uDC00', one]], true));
// HOST-13 / HOST-14: the decimal form is a number within the caps, canonical.
expectCode('num of a decimal with 1,000,001 fractional digits is E_RANGE', 'E_RANGE', () => Value.num({ neg: false, digits: 1n, scale: 1000001 }));
expectCode('num of a decimal with 1,000,001 integer digits is E_RANGE', 'E_RANGE', () => Value.num({ neg: false, digits: 10n ** 1000000n, scale: 0 }));
expectOk('num of a negative-zero decimal is 0', () => assert.equal(Value.num({ neg: true, digits: 0n, scale: 0 }).dump(), 't"0"'));
for (const bad of [{ neg: false, digits: 7n, scale: -1 }, { neg: false, digits: -5n, scale: 0 }, { neg: false, digits: 'x', scale: 0 }, 5]) {
  expectCode(`num of the malformed decimal ${JSON.stringify(bad, (k, x) => typeof x === 'bigint' ? `${x}n` : x)} is E_BAD_ARG`, 'E_BAD_ARG', () => Value.num(bad));
}
// HOST-16: the constructors copy the arrays they are given.
expectOk('shaped and fromEntries copy their arrays', () => {
  const keys = ['a']; const values = [one]; const entries = [['a', one]];
  const v = Value.shaped(keys, values); const w = Value.fromEntries(entries);
  keys[0] = 'z'; values[0] = two; entries[0][1] = two; entries.push(['b', two]);
  assert.equal(v.dump() + w.dump(), '-{"a"=t"1"}-{"a"=t"1"}');
});
// HOST-17: keys and values pair up; a list's keys are kept.
expectCode('shaped with more values than keys is E_BAD_ARG', 'E_BAD_ARG', () => Value.shaped(['a'], [one, two]));
expectCode('shaped with fewer values than keys is E_BAD_ARG', 'E_BAD_ARG', () => Value.shaped(['a', 'b'], [one]));
expectOk('fromEntries keeps the keys of a list', () => assert.equal(Value.fromEntries([['5', one], ['7', two]], true).dump(), '-{"5"=t"1", "7"=t"2"}'));
// HOST-18: a repeated key is RECORD's last write in its first position; a list's is refused.
expectOk('shaped keeps a repeated key once', () => assert.equal(Value.shaped(['a', 'b', 'a'], [one, two, two]).dump(), '-{"a"=t"2", "b"=t"2"}'));
expectCode('fromEntries rejects a repeated list key', 'E_BAD_ARG', () => Value.fromEntries([['5', one], ['5', two]], true));
// HOST-20: a malformed call is E_BAD_ARG, never a TypeError or RangeError.
for (const [what, f] of [['Value.int(1.5)', () => Value.int(1.5)], ['Value.int(NaN)', () => Value.int(NaN)],
  ['Value.text(5)', () => Value.text(5)], ['Value.list("x")', () => Value.list('x')], ['Value.list([1])', () => Value.list([1])],
  ['Value.shaped(["a"], ["1"])', () => Value.shaped(['a'], ['1'])], ['Value.fromNative(1e308)', () => Value.fromNative(1e308)],
  ['Value.fromNative(Infinity)', () => Value.fromNative(Infinity)], ['Value.fromNative(Symbol())', () => Value.fromNative(Symbol('s'))]]) {
  expectCode(`${what} is E_BAD_ARG`, 'E_BAD_ARG', f);
}
// SPEC §2: E_UTF8 for invalid source is reported at the first invalid unit, in code
// points of the valid prefix -- never 0:0:0. A valid surrogate pair is ONE code point.
const utf8At = (name, src, line, col, offset) => expectOk(name, () => {
  let err = null;
  try { compile(src); } catch (e) { err = e; }
  assert.ok(err && err.code === 'E_UTF8', `expected E_UTF8, got ${err && err.code}`);
  assert.deepEqual([err.line, err.col, err.offset], [line, col, offset]);
});
utf8At('lone high surrogate at the start', '\ud800', 1, 1, 0);
utf8At('lone low surrogate at the start', '\udc00', 1, 1, 0);
utf8At('lone surrogate after a newline', '1 +\n \ud800', 2, 2, 5);
utf8At('lone low surrogate in the middle', '"ab\udc00c"', 1, 4, 3);
utf8At('high surrogate followed by a non-surrogate', '1 + \ud800x', 1, 5, 4);
utf8At('valid pair counts as one code point', '"\u{1F600}" & \udc00', 1, 7, 6);
utf8At('pair on an earlier line, lone surrogate on the next', '"\u{1F600}"\n\ud800', 2, 1, 4);
const bytesAt = (name, bytes, line, col, offset) => expectOk(name, () => {
  let err = null;
  try { decodeSource(Uint8Array.from(bytes)); } catch (e) { err = e; }
  assert.ok(err && err.code === 'E_UTF8', `expected E_UTF8, got ${err && err.code}`);
  assert.deepEqual([err.line, err.col, err.offset], [line, col, offset]);
});
const q = 0x22;
bytesAt('bad start byte', [0xff], 1, 1, 0);
bytesAt('bad byte inside a literal', [q, 0x61, 0xff, 0x62, q], 1, 3, 2);
bytesAt('bad byte on line two', [0x31, 0x2b, 0x0a, 0x20, q, 0x61, 0xff], 2, 4, 6);
bytesAt('after a multibyte character', [q, 0xc5, 0x82, 0xff], 1, 3, 2);
bytesAt('truncated sequence at end', [q, 0xe2, 0x82], 1, 2, 1);
bytesAt('overlong encoding', [q, 0xc0, 0x80], 1, 2, 1);
bytesAt('encoded surrogate', [q, 0xed, 0xa0, 0x80], 1, 2, 1);
bytesAt('above U+10FFFF', [q, 0xf4, 0x90, 0x80, 0x80], 1, 2, 1);
bytesAt('after a four-byte character', [0xf0, 0x9f, 0x98, 0x80, 0xff], 1, 2, 1);
expectOk('valid bytes decode unchanged, CR and CRLF included', () =>
  assert.equal(decodeSource(Uint8Array.from([0x61, 0x0d, 0x0a, 0xc5, 0x82])), 'a\r\n\u0142'));
// --- T02/T03 remediation (review 2026-09-29): value ownership at the boundary ---
// JS-C20: the sign of a decimal record is a boolean, not whatever `!!` makes of it.
for (const neg of [undefined, 1, 'yes', null]) {
  expectCode(`JS-C20: Value.num with neg=${String(neg)} is E_BAD_ARG`, 'E_BAD_ARG',
    () => Value.num({ neg, digits: 5n, scale: 0 }));
}
expectOk('JS-C20: Value.num({ neg: true, ... }) is negative', () =>
  assert.equal(Value.num({ neg: true, digits: 5n, scale: 0 }).scalar, '-5'));
// JS-C33: the Value owns its decimal; mutating the caller's record afterwards changes nothing.
expectOk('JS-C33: Value.num copies the caller\'s decimal', () => {
  const d = { neg: false, digits: 5n, scale: 0 };
  const v = Value.num(d);
  d.digits = 99999n; d.scale = 2; d.neg = true;
  assert.equal(v.scalar, '5');
  assert.equal(compile('A + 1').run({ A: v }).scalar, '6');
});
// JS-C37: setting the text drops the cached number.
expectOk('JS-C37: the scalar setter invalidates the cached decimal', () => {
  const w = Value.num('12');
  w.scalar = 'abc';
  assert.equal(w.looksNumeric(), false);
  assert.equal(w.eql(Value.text('abc')), true);
});
// JS-C21: a sparse array holds NULLs, not empty slots.
expectOk('JS-C21: fromNative of a sparse array is a list with NULL in the holes', () => {
  const v = Value.fromNative([1, , 3]);   // eslint-disable-line no-sparse-arrays
  assert.equal(v.dump(), '-{"1"=t"1", "2"=-, "3"=t"3"}');
  assert.equal(compile('COUNT(DEDUPE(L))').run({ L: [1, , 3] }).asText(), '3');
  assert.equal(compile('COUNT(SORT(L))').run({ L: [1, , 3] }).asText(), '3');
});
// JS-C34: only plain objects, arrays, strings, bigints, booleans, integers, Uint8Array, Value.
for (const [name, make] of [
  ['Date', () => new Date()], ['Map', () => new Map([[1, 2]])], ['Set', () => new Set([1])],
  ['ArrayBuffer', () => new ArrayBuffer(2)], ['Int8Array', () => new Int8Array([1, 2])],
  ['a class instance', () => new (class Point { constructor() { this.x = 1; } })()],
  ['a function', () => () => 1], ['a symbol', () => Symbol('s')],
]) {
  expectCode(`JS-C34: fromNative(${name}) is E_BAD_ARG`, 'E_BAD_ARG', () => Value.fromNative(make()));
}
expectOk('JS-C34: a null-prototype object is still a record', () =>
  assert.equal(Value.fromNative(Object.assign(Object.create(null), { a: 1 })).dump(), '-{"a"=t"1"}'));
// JS-C35: whole numbers only (JS cannot tell 3 from 3.0); a fraction is a float (spec 8).
for (const x of [0.5, 0.1 + 0.2, -2.25, 1e21, NaN, Infinity]) {
  expectCode(`JS-C35: fromNative(${x}) is E_BAD_ARG`, 'E_BAD_ARG', () => Value.fromNative(x));
}
expectOk('JS-C35: whole-valued numbers are accepted', () => {
  assert.equal(Value.fromNative(3).scalar, '3');
  assert.equal(Value.fromNative(3.0).scalar, '3');
  assert.equal(Value.fromNative(-0).scalar, '0');
});
// JS-C36: toNative refuses what a JS object would reorder, so the inverse holds for the rest.
expectCode('JS-C36: toNative of a record with keys "b","2","a" has no native form', 'E_BAD_ARG',
  () => compile('RECORD("b", 1, "2", 2, "a", 3)').run({}).toNative());
expectCode('JS-C36: descending position-like keys have no native form', 'E_BAD_ARG',
  () => compile('RECORD("3", 1, "2", 2)').run({}).toNative());
expectOk('JS-C36: ascending position-like keys first round-trip', () => {
  const v = compile('RECORD("2", 1, "10", 2, "b", 3, "a", 4)').run({});
  assert.equal(Value.fromNative(v.toNative()).dump(), v.dump());
});
expectOk('JS-C36: entries() keeps the order toNative cannot', () => {
  const v = compile('RECORD("b", 1, "2", 2, "a", 3)').run({});
  assert.equal(Value.fromEntries(v.entries()).dump(), v.dump());
});
// Gate triage: a value with children and no scalar sorts by scalar context (SPEC 3.2/7.3).
{
  const { evaluate } = await import('../js/src/sel.mjs');
  const rows = 'LIST(RECORD("k", 3, "v", "c"), RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"))';
  const ties = 'LIST(RECORD("k", 1, "v", "a"), RECORD("k", 2, "v", "b"), RECORD("k", 1.0, "v", "c"), RECORD("k", "1", "v", "d"))';
  const joined = (e) => evaluate(`JOIN(MAP(${e}, _["v"]), ",")`).scalar;
  expectOk('records sort by their first field', () => {
    assert.equal(joined(`${rows} .> SORT()`), 'a,b,c');
    assert.equal(joined(`${rows} .> SORT_DESC()`), 'c,b,a');
  });
  expectOk('tied records keep input order in both directions', () => {
    assert.equal(joined(`${ties} .> SORT()`), 'a,c,d,b');
    assert.equal(joined(`${ties} .> SORT_DESC()`), 'b,a,c,d');
    assert.equal(joined(`${ties} .> TOP_DESC(4)`), 'b,a,c,d');
  });
  expectOk('a record ranks by the kind of its first field', () => {
    assert.equal(joined('LIST(RECORD("k", "b", "v", "b"), RECORD("k", 5, "v", "n"), RECORD("k", "a", "v", "a")) .> SORT()'), 'n,a,b');
    assert.equal(joined('LIST(RECORD("k", 5, "v", "n"), RECORD("k", TRUE, "v", "t"), RECORD("k", FALSE, "v", "f")) .> SORT()'), 'f,t,n');
  });
}
// JS-C38: the digit-cap prefilter is derived from the limit, not typed in.
{
  const { intLimitShift } = await import('../js/src/decimal.mjs');
  const { MAX_INT_DIGITS } = await import('../js/src/decimal.mjs');
  expectOk('JS-C38: intLimitShift is the largest S with 2^S <= 10^N', () => {
    for (const n of [1, 2, 3, 5, 18, 19, 100, 1000, MAX_INT_DIGITS]) {
      const s = intLimitShift(n);
      const cap = 10n ** BigInt(n);
      assert.ok((1n << s) <= cap, `2^${s} > 10^${n}`);
      assert.ok((1n << (s + 1n)) > cap, `2^${s + 1n} <= 10^${n}`);
    }
  });
}
// CEIL / FLOOR carry past the digit cap: E_RANGE at the call, not 0:0.
{
  const big = 'REPEAT("9", 1000000) & ".5"';
  for (const [fn, src] of [['CEIL', `CEIL(${big})`], ['FLOOR', `FLOOR("-" & ${big})`]]) {
    expectOk(`${fn} carry past MAX_INT_DIGITS is E_RANGE at the call`, () => {
      let err = null;
      try { compile(src).run({}); } catch (e) { err = e; }
      assert.ok(err, 'no error');
      assert.equal([err.code, err.line, err.col].join(':'), 'E_RANGE:1:1');
    });
  }
}
// T03: the collectors copy what they collect (spec 3.4); a constructor's depth
// is checked at the node that builds the value.
expectOk('T03: MAP/FILTER/TOP/SORT/BUCKET results are independent of their source', () => {
  for (const [name, src] of [
    ['MAP', 'MAP(X, _)'], ['FILTER', 'FILTER(X, TRUE)'], ['SORT', 'SORT(X)'],
    ['TOP', 'TOP(X, 1)'], ['TOP_BY', 'TOP_BY(X, _["k"], 1)'],
    ['BUCKET', 'BUCKET(X, _["k"], _)'],
  ]) {
    const out = compile(`X = LIST(RECORD("k", 1)); R = ${src}; X[1]["k"] = 9; R`).run({});
    assert.ok(!out.dump().includes('t"9"'), `${name} result changed with its source: ${out.dump()}`);
  }
});
expectOk('T03: LIST(A) past the depth cap is E_DEPTH at the LIST call', () => {
  const src = 'A' + '[1]'.repeat(199) + ' = 1; LIST(A); 7';
  let err = null;
  try { compile(src).run({}); } catch (e) { err = e; }
  assert.ok(err && err.code === 'E_DEPTH', String(err));
  assert.equal(err.col, 1 + ('A' + '[1]'.repeat(199) + ' = 1; ').length);
});
expectOk('T03: assigning a value past the cap (path + depth) is E_DEPTH at the target', () => {
  const src = 'A' + '[1]'.repeat(150) + ' = 1; B[1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1]'
    + '[1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1][1] = A; 7';
  let err = null;
  try { compile(src).run({}); } catch (e) { err = e; }
  assert.ok(err && err.code === 'E_DEPTH', String(err));
});

// The command line, through a child process: bytes in, nothing translated.
{
  const dir = mkdtempSync(join(tmpdir(), 'sel-cli-'));
  const cli = resolve('js/bin/sel.mjs');
  const run = (name, bytes) => {
    const f = join(dir, name);
    writeFileSync(f, Buffer.from(bytes));
    return spawnSync(process.execPath, [cli, f], { encoding: 'utf8' });
  };
  try {
    expectOk('CLI: invalid byte is E_UTF8 at its position', () => {
      const r = run('bad.sel', [q, 0x61, 0xff, 0x62, q]);
      assert.equal(r.status, 1);
      assert.match(r.stderr, /^E_UTF8 at line 1 column 3/);
      assert.equal(r.stdout, '');
    });
    expectOk('CLI: CRLF inside a literal is kept', () => {
      const r = run('crlf.sel', Buffer.from('A = "a\r\nb";\nLEN(A)'));
      assert.equal(r.stdout, '4\n');
    });
    expectOk('CLI: a CR is not a line end', () => {
      const r = run('cr.sel', Buffer.from('A = 1 # c\r+ 2\r\nA'));
      assert.match(r.stderr, /^E_SYNTAX at line 2 column 1/);
    });
    expectOk('CLI: stdin lines are decoded strictly too', () => {
      const r = spawnSync(process.execPath, [cli], { input: Buffer.from([q, 0xff, q, 0x0a, 0x31, 0x2b, 0x31, 0x0a]) });
      assert.match(String(r.stderr), /^E_UTF8 at line 1 column 2/);
      assert.match(String(r.stdout), /^2\n/);
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}
// --- T04: evaluation order, optimiser transparency, bounded analysis ---------
{
  const { registerFunction, register } = await import('../js/src/sel.mjs');
  const observe = (source, input = {}) => {
    const answer = (fn) => {
      try { return fn().dump(); } catch (e) { return `!${e.code ?? e.name}@${e.line}:${e.col}`; }
    };
    const program = compile(source);
    const plain = answer(() => evalNode(program.ast, new Context(Value.fromNative(input))));
    const planned = answer(() => program.run(Value.fromNative(input)));
    const again = answer(() => program.run(Value.fromNative(input)));
    return { plain, planned, again };
  };
  const same = (name, source, input, want) => expectOk(name, () => {
    const o = observe(source, input);
    assert.equal(o.planned, o.plain, `plan ${o.planned} vs plain ${o.plain}`);
    assert.equal(o.again, o.plain, `second run ${o.again} vs plain ${o.plain}`);
    if (want !== undefined) assert.equal(o.plain, want);
  });
  // JS-C9: evaluate all operands, then coerce (SPEC 6.2); the plan is invisible.
  same('math plan: "abc" + 1/0 is the division error', '"abc" + 1/0', {}, '!E_DIV_ZERO@1:10');
  same('math plan: later undefined beats earlier text', 'A = "x"; A + B', {}, '!E_UNDEF_VAR@1:14');
  same('math plan: MAX later argument first', 'MAX(TRUE, U)', {}, '!E_UNDEF_VAR@1:11');
  same('math plan: MIN third argument before second coercion', 'MIN(1, "x", Y)', {}, '!E_UNDEF_VAR@1:13');
  same('math plan: ROUND scale evaluated before x is coerced', 'ROUND("x", Y)', {}, '!E_UNDEF_VAR@1:12');
  same('math plan: pending operand sees a later mutation', 'A = (1, 2); A + LEN((A[1] = 10; "ab"))', {}, 't"12"');
  same('math plan: coalesce over arithmetic matches comparison', '(NULL - MISSING) ?? 7', {}, 't"7"');
  same('math plan: one-operand MAX still coerces', 'MAX("x")', {}, '!E_NOT_NUM@1:5');
  same('math plan: x + 0 does not move the coercion', 'A = "7"; (A + 0) + (A = "8"; 1)', {}, 't"8"');
  // JS-C13: SORT + TAKE fuse only for a literal count >= 1, keys first.
  same('SORT_BY + TAKE(0) still evaluates the key', 'LIST(RECORD("a",1)) .> SORT_BY(_["zz"]) .> TAKE(0)', {}, '!E_NO_KEY@1:33');
  same('SORT_BY key error precedes an invalid count', 'LIST(RECORD("a",1)) .> SORT_BY(_["zz"]) .> TAKE(0.5)', {}, '!E_NO_KEY@1:33');
  same('a count with a side effect runs after the keys', 'X = 1; LIST(3,1,2) .> SORT_BY(_ * X) .> TAKE((X = -1; 2))', {});
  expectOk('SORT + TAKE(2) with a literal count still fuses to TOP', () => {
    const ast = compile('LIST(3,1,2) .> SORT() .> TAKE(2)').physicalAst();
    assert.equal(ast.name, 'TOP');
  });
  expectOk('SORT + TAKE(0) or TAKE(expr) does not fuse', () => {
    assert.equal(compile('LIST(3,1,2) .> SORT() .> TAKE(0)').physicalAst().name, 'TAKE');
    const seq = compile('N = 2; LIST(3,1,2) .> SORT() .> TAKE(N)').physicalAst();
    assert.equal(seq.items.at(-1).name, 'TAKE');
  });
  // PHP-C11 twin: a bare variable or literal predicate can still raise.
  same('FILTER + FILTER keeps the first error (bare variable)', 'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)', {}, '!E_DIV_ZERO@1:25');
  same('FILTER + FILTER keeps the first error (literal)', 'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(1)', {}, '!E_DIV_ZERO@1:25');
  // The rewritten pipeline keeps the outer node's position for an operator over it.
  same('position under NOT after TAKE + TAKE', 'NOT TAKE(TAKE(LIST(1), 3), 2)', {}, '!E_NOT_BOOL@1:5');
  same('position under NOT after FILTER(x, TRUE)', 'NOT FILTER(LIST(1), TRUE)', {}, '!E_NOT_BOOL@1:5');
  same('position under NOT after SORT + TAKE', 'X = LIST(1); NOT TAKE(SORT(X), 1)', {}, '!E_NOT_BOOL@1:18');
  // JS-C39: a fused pair spends what the two stages spend.
  {
    const terms = (n) => Array(n).fill('_["a"] == 1').join(' AND ');
    for (const n of [190, 196, 197]) {
      same(`FILTER in FILTER, ${n}-term predicate: fused answer is the plain answer`,
        `FILTER(FILTER(T, _["a"] > 0), ${terms(n)})`, { T: [{ a: 1 }] });
    }
    // The logical (SQL-side) optimiser must not fuse a pair whose second
    // predicate would pass the cap once the joining AND is added.
    expectOk('the logical optimiser leaves a pair whose fusion would pass the cap', () => {
      const boundary = (n) => `FILTER(FILTER(T, _["a"] > 0), ${terms(n)})`;
      const stepsOf = (n) => unwindPipeline(optimizeAstLogical(parse(boundary(n)))).steps.length;
      assert.equal(stepsOf(190), 1, 'a shallow pair fuses');
      assert.equal(stepsOf(197), 2, 'a pair at the cap stays two FILTERs');
      const fused = optimizeAstLogical(parse(boundary(197)));
      assert.doesNotThrow(() => evalNode(fused, new Context(Value.fromNative({ T: [{ a: 1 }] }))));
    });
  }
  // JS-C12: an aggregate body as long as its source is never a host RangeError.
  {
    const chain = (n, v) => Array(n).fill(v).join('+');
    const conj = (n, v) => Array(n).fill(v).join(' AND ');
    for (const [name, src] of [
      ['MAP', (n) => `MAP(T, ${chain(n, '_["a"]')})`],
      ['FILTER', (n) => `FILTER(T, ${conj(n, '_["a"] == 1')})`],
      ['SUM', (n) => `SUM(T, ${chain(n, '_["a"]')})`],
      ['TOP_BY', (n) => `TOP_BY(T, ${chain(n, '_["a"]')}, 1)`],
      ['BUCKET', (n) => `BUCKET(T, ${chain(n, '_["a"]')}, COUNT(_))`],
      ['LINK key', (n) => `LINK(T, T, ${chain(n, '_1["a"]')} == _2["a"])`],
      ['FILTER over LINK', (n) => `FILTER(LINK(T, T, _1["a"] == _2["a"]), ${conj(n, '_["a"] == 1')})`],
    ]) {
      for (const n of [5000, 30000]) {
        expectCode(`${name}: a ${n}-term body is E_DEPTH, not a RangeError`, 'E_DEPTH', () => {
          compile(src(n)).run(Value.fromNative({ T: [{ a: 1 }] }));
        });
      }
    }
  }
  // JS-C42: a shipped builtin cannot be replaced through the public register.
  expectOk('register refuses ABS; ABS still runs the builtin everywhere', () => {
    let refused = null;
    try { register('ABS', 1, 1, () => Value.text('overridden')); } catch (e) { refused = e; }
    assert.ok(refused instanceof RangeError, 'expected a RangeError');
    assert.equal(observe('ABS(-3)').planned, 't"3"');
    assert.equal(observe('ABS("x")').planned, '!E_NOT_NUM@1:5');
  });
  expectOk('register still adds and replaces a host function of its own', () => {
    register('T04_HOSTFN', 0, 0, () => Value.text('one'));
    assert.equal(observe('T04_HOSTFN()').planned, 't"one"');
    register('T04_HOSTFN', 0, 0, () => Value.text('two'));
    assert.equal(observe('T04_HOSTFN()').planned, 't"two"');
  });
  expectOk('register refuses a reserved word', () => {
    assert.throws(() => register('NULL', 0, 0, () => Value.text('x')), RangeError);
  });
}

// --- T05/T06/T07: relational edges, regex portability, size caps -------------
{
  const run = (source, input = {}) => {
    try { return compile(source).run(Value.fromNative(input)).dump(); } catch (e) { return `!${e.code ?? e.name}@${e.line}:${e.col}`; }
  };
  const isCode = (name, source, want) => expectOk(name, () => {
    const got = run(source);
    assert.ok(got.startsWith(`!${want}`), `${source.slice(0, 60)} => ${got.slice(0, 80)}`);
  });
  const equal = (name, source, want) => expectOk(name, () => assert.equal(run(source), want));

  // JS-C1: an aggregate visits a snapshot; a body that grows its source neither
  // extends the walk nor invalidates what it stands on.
  equal('MAP over a list its body appends to visits the snapshot', 'A = (1, 2); COUNT(MAP(A, A[COUNT(A) + 1] = 0))', 't"2"');
  equal('FILTER over a record its body adds keys to visits the snapshot', 'R = RECORD("a", 1); R["b"] = 2; COUNT(FILTER(R, (R[_K & "x"] = 1; TRUE)))', 't"2"');
  equal('TOP_BY over a list its key body appends to visits the snapshot', 'A = (1, 2); COUNT(TOP_BY(A, (A[COUNT(A) + 1] = 0; _), 5))', 't"2"');
  equal('an overwritten later element is still visited as it was', 'A = (1, 2, 3); SUM(A, (A[3] = 100; _))', 't"6"');
  // JS-C2: one total order, by kind then by value.
  equal('SORT ranks NULL < BOOL < numbers < text < BIN and is stable',
    'LIST("10", "9", "1a", "", " 2", "-0", "1e3", "007", "7", "0", FROM_HEX("00ff"), TRUE, FALSE, NULL) .> SORT() .> MAP(IF(_ EQL NULL, "N", IF(_ EQL FALSE, "F", IF(_ EQL TRUE, "T", IF(_ EQL FROM_HEX("00ff"), "B", IF(ISNUM(_), "n" & _, "s" & _)))))) .> JOIN(",")',
    't"N,F,T,n-0,n0,n007,n7,n9,n10,s,s 2,s1a,s1e3,B"');
  equal('numeric-looking text sorts by value before other text', 'LIST("10", "9", "1a") .> SORT() .> JOIN(",")', 't"9,10,1a"');
  equal('SORT_DESC reverses the ranks and keeps ties in input order', 'LIST("7", "007", "1a") .> SORT_DESC() .> JOIN(",")', 't"1a,7,007"');
  // The direction and the count are evaluated and checked even on nothing.
  isCode('SORT_BY validates its direction on an empty list', 'SORT_BY(LIST(), _ + 0, "X")', 'E_BAD_ARG');
  isCode('SORT_BY validates its direction on NULL', 'SORT_BY(NULL, _, "UP")', 'E_BAD_ARG');
  isCode('TOP_BY validates its direction on an empty list', 'TOP_BY(LIST(), _, "UP", 1)', 'E_BAD_ARG');
  // JS-C49 / JS-C50 / SPEC 7.3: BUCKET.
  equal('BUCKET of a scalar makes one group (bare)', 'COUNT(BUCKET("abc", _))', 't"1"');
  equal('BUCKET of a scalar makes one group (projected)', 'COUNT(BUCKET("abc", _, _))', 't"1"');
  equal('BUCKET of a scalar keeps the scalar as the member', 'BUCKET(5, _, _)["1"]["1"]', 't"5"');
  equal('two keys with one text are one group and lose no row',
    'A = "x"; A["k"] = 1; R = LIST(A, "x", A) .> BUCKET(_); JOIN(LIST(COUNT(R), COUNT(R["x"])), ",")', 't"1,3"');
  // JS-C51: same-named binders — the right shadows the left, both paths agree.
  expectOk('a join on one binder name gives the general path\'s answer', () => {
    const fast = run('T = LIST(RECORD("id", 1, "mgr", 1), RECORD("id", 2, "mgr", 1)); COUNT(LINK(T, T, T["id"] == T["mgr"]))');
    const general = run('T = LIST(RECORD("id", 1, "mgr", 1), RECORD("id", 2, "mgr", 1)); COUNT(LINK(T, T, T["id"] == T["mgr"] AND TRUE))');
    assert.equal(fast, general);
  });
  // JS-C16: a comma-list operand reading both binders is not one-sided.
  expectOk('a comma list in a join key is classified by what it reads', () => {
    const src = (extra) => `A = LIST(RECORD("k", 1)); B = LIST(RECORD("k", 1)); COUNT(LINK(A, B, (1, _1["k"]) == (1, _2["k"])${extra}))`;
    assert.equal(run(src('')), run(src(' AND TRUE')));
  });
  // JS-C14 / JS-C15: cost, not answers.
  expectOk('DEDUPE of many distinct scalars is not quadratic', () => {
    const t0 = Date.now();
    assert.equal(run('COUNT(DEDUPE(SPLIT(REPEAT("a,", 20000) & "b", ",")))'), 't"2"');
    assert.ok(Date.now() - t0 < 3000, `took ${Date.now() - t0} ms`);
  });
  expectOk('a numeric join key with 100,000 trailing fractional zeros is linear', () => {
    const t0 = Date.now();
    const src = 'A = LIST(RECORD("k", "1." & REPEAT("0", 100000))); B = LIST(RECORD("k", "1")); COUNT(LINK(A, B, _1["k"] == _2["k"]))';
    assert.equal(run(src), 't"1"');
    assert.ok(Date.now() - t0 < 3000, `took ${Date.now() - t0} ms`);
  });

  // --- T06 regex ---
  // Raw ('...') literals: a double-quoted one would read {2} as an interpolation.
  const raw = (x) => `'${x.replaceAll("'", "''")}'`;
  const reject = (name, pattern) => {
    isCode(`regex ${name} is refused`, `RMATCH(${raw(pattern)}, 'a')`, 'E_REGEX_SYNTAX');
  };
  const accept = (name, pattern, subject) => equal(`regex ${name} is accepted`, `RMATCH(${raw(pattern)}, ${raw(subject)})`, 'TRUE');
  for (const [n, pat] of [['quantified ^', '^*'], ['quantified $ plus', '$+'], ['quantified anchor braces', '^{2}'],
    ['nullable loop body (a*)*', '(a*)*'], ['nullable loop body (?:a?)+', '(?:a?)+'], ['empty branch in a loop', '(|a)+'],
    ['optional capture in a loop', '(?:(a)|b)*'], ['capture under ? in a loop', '(?:(a)?b)+'],
    ['class escape range end', '[\\d-z]'], ['class escape range start', '[a-\\s]'], ['POSIX class late', '[a[:digit:]'],
    ['POSIX collating form', '[[.x.]]'], ['PCRE verb', '(*FAIL)'], ['nothing to repeat', '*a'], ['double quantifier', 'a**'],
    ['unmatched )', 'a)'], ['depth 201', '(?:'.repeat(201) + 'a' + ')'.repeat(201)],
    ['1001 groups', '(a)'.repeat(1001)]]) reject(n, pat);
  accept('(\\d*)? outside a loop', '^(\\d*)?$', '12');
  accept('[\\d-] hyphen last', '^[\\d-]$', '-');
  accept('[[.] literal bracket and dot', '^[[.]$', '.');
  accept('a run of identical atoms (folded to a count)', '^(?:ab){3}a{9}$', 'ababab' + 'a'.repeat(9));
  expectOk('the 65,533-letter pattern matches; 65,535 is refused', () => {
    assert.equal(run(`RMATCH("^" & REPEAT("a", 65533) & "$", REPEAT("a", 65533))`), 'TRUE');
    assert.ok(run(`RMATCH("^" & REPEAT("a", 65535) & "$", "a")`).startsWith('!E_REGEX_SYNTAX'));
  });
  expectOk('flags: only lowercase i', () => {
    for (const f of ['I', 'İ', 'ı', 'K']) assert.ok(run(`RMATCH("a", "A", ${JSON.stringify(f)})`).startsWith('!E_BAD_ARG'), f);
    assert.equal(run('RMATCH("^a+$", "AaA", "i")'), 'TRUE');
  });
  expectOk('a literal pattern is checked when the program compiles', () => {
    for (const src of ['IF(FALSE, RMATCH("(?=a)", "a"), 1)', "IF(FALSE, RFIND('(?=a)', 'a'), 1)", "IF(FALSE, RREPLACE('(?=a)', '-', 'a'), 1)", "IF(FALSE, RGROUPS('(?=a)', 'a'), 1)"]) {
      assert.throws(() => compile(src), (e) => e.code === 'E_REGEX_SYNTAX', src);
    }
    // ... but a computed pattern in a dead branch is fine.
    assert.doesNotThrow(() => compile('IF(FALSE, RMATCH("(?" & "=a)", "a"), 1)'));
  });
  expectOk('the compiled-pattern cache is bounded', () => {
    for (let i = 0; i < 600; i++) run(`RMATCH('a{${i + 1}}', 'a')`);
    // No way to read the size from outside: the assertion is that 600 distinct
    // patterns run and still answer correctly after evictions.
    assert.equal(run("RMATCH('a{1}', 'a')"), 'TRUE');
  });
  expectOk('a long subject is an answer or a SEL error, never a RangeError', () => {
    const got = run('RMATCH("^(?:a|b)*$", REPEAT("a", 2000000))');
    assert.ok(got === 'TRUE' || got.startsWith('!E_'), got.slice(0, 60));
  });
  equal('RREPLACE walks empty matches (P3)', 'RREPLACE("a*", "-", "baac")', 't"-b--c-"');
  equal('RREPLACE lazy empty match then match', 'RREPLACE("b*?", "-", "abb")', 't"-a-b-b-"');

  // --- T07 caps ---
  isCode('REPEAT past the text cap is E_RANGE at the call', 'REPEAT("a", 16777217)', 'E_RANGE');
  equal('REPEAT of the empty text with a huge count is empty', 'REPEAT("", 99999999999999999999)', 't""');
  isCode('REPEAT with a 400-digit count is E_RANGE, not a RangeError', 'REPEAT("a", 1 & REPEAT("0", 400))', 'E_RANGE');
  isCode('PADL past the cap is E_RANGE', 'PADL("a", 16777217, "x")', 'E_RANGE');
  isCode('concat past the cap is E_RANGE at the operator', 'REPEAT("a", 16777216) & "b"', 'E_RANGE');
  equal('concat at the cap is fine', 'LEN(REPEAT("a", 16777215) & "b")', 't"16777216"');
  isCode('REPLACE growing past the cap is E_RANGE', 'REPLACE("a", REPEAT("b", 200000), REPEAT("a", 200))', 'E_RANGE');
  equal('REPLACE with a 200,000-code-point replacement works (no argument spread)', 'LEN(REPLACE("a", REPEAT("b", 200000), "a"))', 't"200000"');
  isCode('JOIN past the cap is E_RANGE', 'JOIN(SPLIT(REPEAT("a,", 100) & "a", ","), REPEAT("b", 10000000))', 'E_RANGE');
  isCode('SPLIT past the collection cap is E_RANGE', 'COUNT(SPLIT(REPEAT("a,", 1000000) & "a", ","))', 'E_RANGE');
  isCode('BTL past the collection cap is E_RANGE', 'COUNT(BTL(TO_UTF8(REPEAT("a", 1000001))))', 'E_RANGE');
  isCode('TO_HEX past the cap is E_RANGE', 'TO_HEX(TO_UTF8(REPEAT("a", 8388609)))', 'E_RANGE');
  isCode('RREPLACE past the cap is E_RANGE', 'RREPLACE("a", REPEAT("b", 40), REPEAT("a", 500000))', 'E_RANGE');
  isCode('the comma flatten past the collection cap is E_RANGE',
    'COUNT((SPLIT(REPEAT("a,", 599999) & "a", ","), SPLIT(REPEAT("a,", 599999) & "a", ",")))', 'E_RANGE');
  isCode('LINK rows past the collection cap are E_RANGE',
    'A = SPLIT(REPEAT("a,", 1000) & "a", ","); B = SPLIT(REPEAT("b,", 999) & "b", ","); COUNT(LINK(A, B, TRUE))', 'E_RANGE');
  equal('LTB of an empty list is the empty BIN', 'BLEN(LTB(BTL("")))', 't"0"');
  equal('LTB takes an integral value of any scale', 'TO_HEX(LTB(LIST(65, 1.0, "1.0", 255.00)))', 't"410101ff"');
  isCode('LTB of a fractional byte is E_NOT_INT', 'LTB(LIST(1.5))', 'E_NOT_INT');
  equal('PATH with an empty path is the target', 'PATH(LIST(1, 2), "") .> COUNT()', 't"2"');

  // --- P5: the static exponential-ambiguity rule (SPEC 7.8; JS-C6) -----------
  const sq = (pat) => `'${pat.replaceAll("'", "''")}'`;
  const verdict = (pat, flags = '') => run(`RMATCH(${sq(pat)}, ""${flags ? `, "${flags}"` : ''})`);
  const refused = (pat, flags = '') => verdict(pat, flags).startsWith('!E_REGEX_SYNTAX');
  const REJECT = String.raw`(a+)+$
(a|aa)+$
(a|b|ab)*c
(?:a+|b)*(?:a+)*c
(.+)+x
([a-z]+)*$
(\w+\s?)*$
(\w+\s*)*$
([a-zA-Z]+)*\d
(?:[a-z]+|\d+)*$
(\d+\d*)+$
([\w.-]+\.)+$
(x+x+)+y
(?:\d|\d\d)+$
(?:\s|\s\s)+$
(?:.|\n)*x
(?:a|a)*$
(\d{1,3},?)+$
^(([a-z])+.)+[A-Z]([a-z])+$
(?:\s*,\s*)*x
(?:[ab]|[bc])*$
(\s*\w+\s*)*$
(?:x|xx|xxx)+y
^(\w+[-.]?)+@
(?:[\d.]+,?)+$
(a+){2,}$
(?:(?:a|b)+c?)+$
((a+)b?)*$
(?:a+)+?b
(?:a{1,20}){1,20}b
(?:\s*\w+\s*,?)*x
(?:(?:a?|b?)c)*d
(?:(?:a*)?c)*d
^(?:a+){2,}$
^(?:a|b|ab)+$`.split('\n');
  const ACCEPT = String.raw`(\d+,)+
(?:ab|cd)*
^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$
(\w+\s)*
([a-z]+-)*[a-z]+
(?:a|b)*
a*b*c*
^\d{3}-\d{3}-\d{4}$
^(\d{1,3}\.){3}\d{1,3}$
^[+-]?\d+(?:\.\d+)?$
^"(?:[^"\\]|\\.)*"$
^[a-z0-9]+(?:[-_.][a-z0-9]+)*$
^(?:https?://)?(?:[\w-]+\.)+[a-z]{2,}(?:/\S*)?$
^(?:[^,]*,)*[^,]*$
^\s*(\w+)\s*=\s*(.*?)\s*$
(?:\r\n|\n)*
^(?:[a-z]+\d+)*$
^[A-Z]{2}\d{2}(?: ?\d{4}){4,7}$
^(?:ab|ac)*$
^(?:ab|a)*$
(?:foo|foobar)*
^(?:\d{3}){1,2}$
(?:a{2}){3}
^(a{300}){300}$
(?:a{60000}){60000}
^[a-z]+(?:[A-Z][a-z]+)*$
^(?:[A-Z][a-z0-9]+)+$
(?:[a-z]|[A-Z])+$
^(?:[0-9]*|[a-z]*)$
^(\d*)?$
(^|[^0-9A-Za-z_])foo($|[^0-9A-Za-z_])
^(?:a+b)+$
^[a-z]+(\.[a-z]+)*$
^(a|b)*$
^(?:a|b)+c?$
^[^<>]*(?:<[^<>]*>[^<>]*)*$
^(.*),(.*),(.*),(.*)$
a*a*$
^a{65535}$
^(?:ab){65535}$
^[a-z]{1,65535}$`.split('\n');
  for (const pat of REJECT) expectOk(`P5 refuses ${pat}`, () => assert.ok(refused(pat), `${pat} was accepted`));
  for (const pat of ACCEPT) {
    expectOk(`P5 accepts ${pat}`, () => assert.ok(!refused(pat), `${pat} => ${verdict(pat)}`));
  }
  for (const pat of ['(?:a|A)+$', '(?:[a-z]|[A-Z])+$', '^[a-z]*(?:[a-c]|[A-C])+$']) {
    // The same pattern twice, flag first and flag second, then the other way: the
    // compile cache is keyed by the flag, so neither verdict may leak into the other.
    expectOk(`P5 folds the i flag and keeps the cache per flag: ${pat}`, () => {
      assert.ok(!refused(pat), 'accepted without i');
      assert.ok(refused(pat, 'i'), 'refused with i');
      assert.ok(!refused(pat), 'still accepted without i');
      assert.ok(refused(pat, 'i'), 'still refused with i');
    });
  }
  expectOk('P5 budget boundaries', () => {
    assert.ok(!refused('(a|a)'.repeat(8) + 'x') && refused('(a|a)'.repeat(9) + 'x'));
    assert.ok(!refused('(?:|)'.repeat(16) + 'x') && refused('(?:|)'.repeat(17) + 'x'));
    assert.ok(!refused('a?'.repeat(7) + 'b') && refused('a?'.repeat(8) + 'b'));
    assert.ok(!refused('(?:a|a){1,8}$') && refused('(?:a|a){1,9}$'));
  });
  expectOk('P5 rejects the classic hostile pattern before any subject exists, quickly', () => {
    const t0 = Date.now();
    assert.equal(run(`RMATCH('^(a+)+$', REPEAT("a", 40) & "!")`).slice(0, 16), '!E_REGEX_SYNTAX@');
    assert.ok(Date.now() - t0 < 2000, 'slow refusal');
  });
  expectOk('P5 analysis stays fast on the size limits', () => {
    const t0 = Date.now();
    for (const pat of ['a'.repeat(65535), '(?:ab){65535}', `${'(a|b)'.repeat(400)}`, '[a-z]'.repeat(30000)]) verdict(pat);
    assert.ok(Date.now() - t0 < 8000, 'slow analysis');
  });
}

// --- T12: host API contract (spec/SPEC.md §8): flow-sensitive dependencies(),
// non-source input, the host-function argument reader, command-line misuse ----
{
  const { registerFunction } = await import('../js/src/sel.mjs');
  const deps = (src) => compile(src).dependencies().join(' ') || '-';
  const cases = [
    ['A + 1; A = 2', 'A'],                          // read before the assignment
    ['A = 1; A + B', 'B'],                          // assigned first: not a dependency
    ['X += 1', 'X'],                                // op= reads its target
    ['A[1] += 1', 'A'],
    ['A[1] = 2', '-'],                              // plain A[k]=x creates A, reads only the index
    ['A[I] = 2', 'I'],
    ['A = A + 1', 'A'],                             // the right side runs before the store
    ['IF(X, A = 1, 0); A', 'A X'],                  // one arm assigns
    ['IF(X, A = 1, A = 2); A', 'X'],                // both arms assign
    ['IF(X, A = 1); A', 'A X'],                     // two-argument IF has an empty else
    ['X AND (A = 1); A', 'A X'],                    // right side of AND is conditional
    ['X OR (A = 1); A', 'A X'],
    ['X ?? (A = 1); A', 'A X'],
    ['X ??? (A = 1); A', 'A X'],
    ['MAP(L, A = _); A', 'A L'],                    // an aggregate body may never run
    ['COND(X, A = 1, Y, A = 2, A = 3); A', 'X Y'],  // every outcome, default included, assigns
    ['COND(X, A = 1, Y, A = 2, 0); A', 'A X Y'],
    ['LEFT("abc", (N = 2)); N', '-'],               // a strict argument always runs
    ['COALESCE(X, (A = 1), 2); A', 'A X'],          // later COALESCE arguments are conditional
    ['GET(T, "k", (A = 1)); A', 'A T'],             // GET's default is conditional
    ['A = 1; MAP(L, A + _)', 'L'],                  // definite before the body
    ['ALL(I, IT, IT > 0)', 'I'],                    // binders are not variables
  ];
  for (const [src, want] of cases) {
    expectOk(`T12 dependencies: ${src} => ${want}`, () => assert.equal(deps(src), want));
  }
  expectOk('T12 dependencies keeps its E_DEPTH cap and position', () => {
    const src = 'A' + '+A'.repeat(300);
    const e = (() => { try { deps(src); } catch (x) { return x; } return null; })();
    assert.ok(e && e.code === 'E_DEPTH', 'want E_DEPTH');
  });
  for (const bad of [12, null, undefined, {}, ['1'], 1n, Symbol.iterator, Buffer.from('1')]) {
    expectCode(`T12 compile(${typeof bad === 'symbol' ? 'symbol' : String(bad)}) is E_BAD_ARG`, 'E_BAD_ARG', () => compile(bad));
  }
  registerFunction('T12_OOB', 1, 3, (a) => a.text(2));
  registerFunction('T12_OOB_VAL', 1, 3, (a) => a.val(7));
  registerFunction('T12_OOB_POS', 1, 3, (a) => { a.posOf(-1); return Value.text('x'); });
  registerFunction('T12_OOB_SYM', 1, 3, (a) => { a.symbol(4); return Value.text('x'); });
  for (const src of ['T12_OOB("x")', 'T12_OOB_VAL("x")', 'T12_OOB_POS("x")', 'T12_OOB_SYM("x")']) {
    expectOk(`T12 host argument read past the count: ${src}`, () => {
      let e = null;
      try { compile(src).run(Value.none()); } catch (x) { e = x; }
      assert.ok(e && e.code === 'E_BAD_ARG', `want E_BAD_ARG, got ${e && (e.code || e.name)}`);
      assert.equal(e.line, 1);          // positioned at the call
    });
  }
  expectOk('T12 a host function may still read an argument the call does have', () => {
    registerFunction('T12_OK', 1, 2, (a) => Value.text(a.count() > 1 ? a.text(1) : a.text(0)));
    assert.equal(compile('T12_OK("a", "b")').run(Value.none()).scalar, 'b');
    assert.equal(compile('T12_OK("a")').run(Value.none()).scalar, 'a');
  });
  // The command line: one plain line on stderr, no stack trace, a small non-zero status.
  const cli = resolve('js/bin/sel.mjs');
  const missing = join(tmpdir(), 'sel-no-such-dir', 'no-such-file.sel');
  for (const [label, argv] of [['-e without operand', ['-e']], ['--deps -e without operand', ['--deps', '-e']],
    ['a missing file', [missing]], ['an unknown option', ['--no-such-flag']]]) {
    expectOk(`T12 CLI misuse: ${label}`, () => {
      const r = spawnSync(process.execPath, [cli, ...argv], { encoding: 'utf8', input: '' });
      assert.ok(r.status > 0 && r.status < 128, `status ${r.status}`);
      assert.equal(r.stdout, '');
      assert.ok(r.stderr.trim().split('\n').length === 1, `not one line: ${r.stderr}`);
      assert.ok(!/node:|file:\/\/\/|Error:|at .*\(/.test(r.stderr), `stack trace: ${r.stderr}`);
    });
  }
}

if (boundary.length) {
  console.error(`JS runtime: ${boundary.length} host-boundary contract(s) broken:\n  ` + boundary.join('\n  '));
  process.exit(1);
}
console.log(`JS runtime: ${checks} checks passed`);
