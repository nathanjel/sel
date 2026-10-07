import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { compile, Value } from '../js/src/sel.mjs';
import { parse } from '../js/src/parser.mjs';
import { evalNode, Context } from '../js/src/eval.mjs';
import { optimizeAstLogical, optimizeAstInMemory, unwindPipeline } from '../js/src/optimizer.mjs';
import { decodeSource, fromCodePoints, toCodePoints } from '../js/src/utf8.mjs';
import { structuralHash, RecordShape } from '../js/src/value.mjs';
import * as DEC from '../js/src/decimal.mjs';
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

// The in-memory optimiser prepares a RECORD's shape ('prepared'); the plain
// parse has none ('generic'); a shape that does not match the keys the call
// evaluates ('stale') must be noticed and not used.
function outcome(source, input, layout) {
  const ast = layout === 'prepared' ? optimizeAstInMemory(parse(source)) : parse(source);
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

// --- the host boundary (spec/SPEC.md §8) ----------------------------------------
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
// Every key survives toNative as an own property, __proto__ included.
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
// The boundary copies, both ways.
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
// Bytes are whole numbers 0..255.
expectOk('Value.bin accepts 0 and 255', () => assert.equal(Value.bin([0, 255]).dump(), 'b00ff'));
for (const bad of [256, -1, 1.5, NaN, '1']) {
  expectCode(`Value.bin rejects ${JSON.stringify(bad)}`, 'E_RANGE', () => Value.bin([bad]));
}
// Every text entering is checked, keys included.
expectCode('Value.text rejects a lone surrogate', 'E_UTF8', () => Value.text('\uD800'));
expectCode('fromNative rejects a lone surrogate', 'E_UTF8', () => Value.fromNative('\uD800'));
expectCode('fromNative rejects a lone-surrogate key', 'E_UTF8', () => Value.fromNative({ ['\uD800']: 'x' }));
expectCode('Value.set rejects a lone-surrogate key', 'E_UTF8', () => Value.none().set('\uDC00', Value.text('x')));
expectOk('a supplementary character is text', () => assert.equal(Value.text('\u{1F600}').dump(), 't"\u{1F600}"'));
// The digit caps hold for native integers (the boundary itself, both sides).
expectOk('Value.int of 1,000,000 digits is a number', () => {
  assert.equal(compile('LEN(A) == 1000000').run({ A: Value.int(10n ** 999999n) }).dump(), 'TRUE');
});
expectCode('Value.int of 1,000,001 digits is E_RANGE', 'E_RANGE', () => Value.int(10n ** 1000000n));
// An over-deep host value cannot be hashed any more than dumped.
for (const src of ['COUNT(DEDUPE(A))', 'COUNT(DISTINCT(A))', 'COUNT(BUCKET(A, _, COUNT(_)))']) {
  expectCode(`${src} over a value nested past the cap`, 'E_DEPTH', () => compile(src).run({ A: deep(250) }));
  expectOk(`${src} just below the cap`, () => assert.equal(compile(src).run({ A: deep(198) }).dump(), 't"1"'));
}
// toNative and fromNative are inverses.
for (const src of ['FILTER(LIST(1,2,3), _ > 1)', 'RECORD("0","a","1","b")', 'FALSE', 'RECORD("a", FALSE)', 'LIST(TRUE, NULL)']) {
  expectOk(`round trip of ${src}`, () => {
    const v = compile(src).run();
    assert.equal(Value.fromNative(v.toNative()).dump(), v.dump());
  });
}
// A compiled program keeps nothing from one run to the next.
expectOk('a compiled program reads the key of each run', () => {
  const p = compile('A[K]'); const A = Value.fromNative({ x: '1', y: '2' });
  assert.equal(p.run({ A, K: 'x' }).dump() + p.run({ A, K: 'y' }).dump(), 't"1"t"2"');
});
// --- every public constructor (spec/SPEC.md §8) ----------------------------
const one = Value.text('1'); const two = Value.text('2');
// fromNative and a run context hold the integer digit cap too.
expectCode('fromNative of a 1,000,001-digit bigint is E_RANGE', 'E_RANGE', () => Value.fromNative(10n ** 1000000n));
expectCode('a run context holding one is E_RANGE', 'E_RANGE', () => compile('LEN(X)').run({ X: 10n ** 1000000n }));
// Keys given side by side are checked like any other text.
expectCode('shaped rejects a lone-surrogate key', 'E_UTF8', () => Value.shaped(['a\uD800'], [one]));
expectCode('fromEntries rejects a lone-surrogate key', 'E_UTF8', () => Value.fromEntries([['a\uD800', one]]));
expectCode('fromEntries rejects one in a list key', 'E_UTF8', () => Value.fromEntries([['\uDC00', one]], true));
// The decimal form is a number within the caps, canonical.
expectCode('num of a decimal with 1,000,001 fractional digits is E_RANGE', 'E_RANGE', () => Value.num({ neg: false, digits: 1n, scale: 1000001 }));
expectCode('num of a decimal with 1,000,001 integer digits is E_RANGE', 'E_RANGE', () => Value.num({ neg: false, digits: 10n ** 1000000n, scale: 0 }));
expectOk('num of a negative-zero decimal is 0', () => assert.equal(Value.num({ neg: true, digits: 0n, scale: 0 }).dump(), 't"0"'));
for (const bad of [{ neg: false, digits: 7n, scale: -1 }, { neg: false, digits: -5n, scale: 0 }, { neg: false, digits: 'x', scale: 0 }, 5]) {
  expectCode(`num of the malformed decimal ${JSON.stringify(bad, (k, x) => typeof x === 'bigint' ? `${x}n` : x)} is E_BAD_ARG`, 'E_BAD_ARG', () => Value.num(bad));
}
// The constructors copy the arrays they are given.
expectOk('shaped and fromEntries copy their arrays', () => {
  const keys = ['a']; const values = [one]; const entries = [['a', one]];
  const v = Value.shaped(keys, values); const w = Value.fromEntries(entries);
  keys[0] = 'z'; values[0] = two; entries[0][1] = two; entries.push(['b', two]);
  assert.equal(v.dump() + w.dump(), '-{"a"=t"1"}-{"a"=t"1"}');
});
// Keys and values pair up; a list's keys are kept.
expectCode('shaped with more values than keys is E_BAD_ARG', 'E_BAD_ARG', () => Value.shaped(['a'], [one, two]));
expectCode('shaped with fewer values than keys is E_BAD_ARG', 'E_BAD_ARG', () => Value.shaped(['a', 'b'], [one]));
expectOk('fromEntries keeps the keys of a list', () => assert.equal(Value.fromEntries([['5', one], ['7', two]], true).dump(), '-{"5"=t"1", "7"=t"2"}'));
// A repeated key is RECORD's last write in its first position; a list's is refused.
expectOk('shaped keeps a repeated key once', () => assert.equal(Value.shaped(['a', 'b', 'a'], [one, two, two]).dump(), '-{"a"=t"2", "b"=t"2"}'));
expectCode('fromEntries rejects a repeated list key', 'E_BAD_ARG', () => Value.fromEntries([['5', one], ['5', two]], true));
// A malformed call is E_BAD_ARG, never a TypeError or RangeError.
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
// --- value ownership at the boundary ---
// The sign of a decimal record is a boolean, not whatever `!!` makes of it.
for (const neg of [undefined, 1, 'yes', null]) {
  expectCode(`Value.num with neg=${String(neg)} is E_BAD_ARG`, 'E_BAD_ARG',
    () => Value.num({ neg, digits: 5n, scale: 0 }));
}
expectOk('Value.num({ neg: true, ... }) is negative', () =>
  assert.equal(Value.num({ neg: true, digits: 5n, scale: 0 }).scalar, '-5'));
// The Value owns its decimal; mutating the caller's record afterwards changes nothing.
expectOk('Value.num copies the caller\'s decimal', () => {
  const d = { neg: false, digits: 5n, scale: 0 };
  const v = Value.num(d);
  d.digits = 99999n; d.scale = 2; d.neg = true;
  assert.equal(v.scalar, '5');
  assert.equal(compile('A + 1').run({ A: v }).scalar, '6');
});
// Setting the text drops the cached number.
expectOk('the scalar setter invalidates the cached decimal', () => {
  const w = Value.num('12');
  w.scalar = 'abc';
  assert.equal(w.looksNumeric(), false);
  assert.equal(w.eql(Value.text('abc')), true);
});
// A sparse array holds NULLs, not empty slots.
expectOk('fromNative of a sparse array is a list with NULL in the holes', () => {
  const v = Value.fromNative([1, , 3]);   // eslint-disable-line no-sparse-arrays
  assert.equal(v.dump(), '-{"1"=t"1", "2"=-, "3"=t"3"}');
  assert.equal(compile('COUNT(DEDUPE(L))').run({ L: [1, , 3] }).asText(), '3');
  assert.equal(compile('COUNT(SORT(L))').run({ L: [1, , 3] }).asText(), '3');
});
// Only plain objects, arrays, strings, bigints, booleans, integers, Uint8Array, Value.
for (const [name, make] of [
  ['Date', () => new Date()], ['Map', () => new Map([[1, 2]])], ['Set', () => new Set([1])],
  ['ArrayBuffer', () => new ArrayBuffer(2)], ['Int8Array', () => new Int8Array([1, 2])],
  ['a class instance', () => new (class Point { constructor() { this.x = 1; } })()],
  ['a function', () => () => 1], ['a symbol', () => Symbol('s')],
]) {
  expectCode(`fromNative(${name}) is E_BAD_ARG`, 'E_BAD_ARG', () => Value.fromNative(make()));
}
expectOk('a null-prototype object is still a record', () =>
  assert.equal(Value.fromNative(Object.assign(Object.create(null), { a: 1 })).dump(), '-{"a"=t"1"}'));
// Whole numbers only (JS cannot tell 3 from 3.0); a fraction is a float (spec 8).
for (const x of [0.5, 0.1 + 0.2, -2.25, 1e21, NaN, Infinity]) {
  expectCode(`fromNative(${x}) is E_BAD_ARG`, 'E_BAD_ARG', () => Value.fromNative(x));
}
expectOk('whole-valued numbers are accepted', () => {
  assert.equal(Value.fromNative(3).scalar, '3');
  assert.equal(Value.fromNative(3.0).scalar, '3');
  assert.equal(Value.fromNative(-0).scalar, '0');
});
// toNative refuses what a JS object would reorder, so the inverse holds for the rest.
expectCode('toNative of a record with keys "b","2","a" has no native form', 'E_BAD_ARG',
  () => compile('RECORD("b", 1, "2", 2, "a", 3)').run({}).toNative());
expectCode('descending position-like keys have no native form', 'E_BAD_ARG',
  () => compile('RECORD("3", 1, "2", 2)').run({}).toNative());
expectOk('ascending position-like keys first round-trip', () => {
  const v = compile('RECORD("2", 1, "10", 2, "b", 3, "a", 4)').run({});
  assert.equal(Value.fromNative(v.toNative()).dump(), v.dump());
});
expectOk('entries() keeps the order toNative cannot', () => {
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
// The digit-cap prefilter is derived from the limit, not typed in.
{
  const { intLimitShift } = await import('../js/src/decimal.mjs');
  const { MAX_INT_DIGITS } = await import('../js/src/decimal.mjs');
  expectOk('intLimitShift is the largest S with 2^S <= 10^N', () => {
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
// The collectors copy what they collect (spec 3.4); a constructor's depth
// is checked at the node that builds the value.
expectOk('MAP/FILTER/TOP/SORT/BUCKET results are independent of their source', () => {
  for (const [name, src] of [
    ['MAP', 'MAP(X, _)'], ['FILTER', 'FILTER(X, TRUE)'], ['SORT', 'SORT(X)'],
    ['TOP', 'TOP(X, 1)'], ['TOP_BY', 'TOP_BY(X, _["k"], 1)'],
    ['BUCKET', 'BUCKET(X, _["k"], _)'],
  ]) {
    const out = compile(`X = LIST(RECORD("k", 1)); R = ${src}; X[1]["k"] = 9; R`).run({});
    assert.ok(!out.dump().includes('t"9"'), `${name} result changed with its source: ${out.dump()}`);
  }
});
expectOk('LIST(A) past the depth cap is E_DEPTH at the LIST call', () => {
  const src = 'A' + '[1]'.repeat(199) + ' = 1; LIST(A); 7';
  let err = null;
  try { compile(src).run({}); } catch (e) { err = e; }
  assert.ok(err && err.code === 'E_DEPTH', String(err));
  assert.equal(err.col, 1 + ('A' + '[1]'.repeat(199) + ' = 1; ').length);
});
expectOk('assigning a value past the cap (path + depth) is E_DEPTH at the target', () => {
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
// --- evaluation order, optimiser transparency, bounded analysis ---------
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
  // Evaluate all operands, then coerce (SPEC 6.2); the plan is invisible.
  same('math plan: "abc" + 1/0 is the division error', '"abc" + 1/0', {}, '!E_DIV_ZERO@1:10');
  same('math plan: later undefined beats earlier text', 'A = "x"; A + B', {}, '!E_UNDEF_VAR@1:14');
  same('math plan: MAX later argument first', 'MAX(TRUE, U)', {}, '!E_UNDEF_VAR@1:11');
  same('math plan: MIN third argument before second coercion', 'MIN(1, "x", Y)', {}, '!E_UNDEF_VAR@1:13');
  same('math plan: ROUND scale evaluated before x is coerced', 'ROUND("x", Y)', {}, '!E_UNDEF_VAR@1:12');
  same('math plan: pending operand sees a later mutation', 'A = (1, 2); A + LEN((A[1] = 10; "ab"))', {}, 't"12"');
  same('math plan: coalesce over arithmetic matches comparison', '(NULL - MISSING) ?? 7', {}, 't"7"');
  same('math plan: one-operand MAX still coerces', 'MAX("x")', {}, '!E_NOT_NUM@1:5');
  same('math plan: x + 0 does not move the coercion', 'A = "7"; (A + 0) + (A = "8"; 1)', {}, 't"8"');
  // SORT + TAKE fuse only for a literal count >= 1, keys first.
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
  // A bare variable or literal predicate can still raise.
  same('FILTER + FILTER keeps the first error (bare variable)', 'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(_)', {}, '!E_DIV_ZERO@1:25');
  same('FILTER + FILTER keeps the first error (literal)', 'LIST(1,2,3) .> FILTER(1 / (_ - 3) < 0) .> FILTER(1)', {}, '!E_DIV_ZERO@1:25');
  // The rewritten pipeline keeps the outer node's position for an operator over it.
  same('position under NOT after TAKE + TAKE', 'NOT TAKE(TAKE(LIST(1), 3), 2)', {}, '!E_NOT_BOOL@1:5');
  same('position under NOT after FILTER(x, TRUE)', 'NOT FILTER(LIST(1), TRUE)', {}, '!E_NOT_BOOL@1:5');
  same('position under NOT after SORT + TAKE', 'X = LIST(1); NOT TAKE(SORT(X), 1)', {}, '!E_NOT_BOOL@1:18');
  // A fused pair spends what the two stages spend.
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
  // An aggregate body as long as its source is never a host RangeError.
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
  // A shipped builtin cannot be replaced through the public register.
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
  // The deprecated public register is registerFunction's strict path (spec §8.1).
  expectOk('register refuses a lazy or binding host function', () => {
    assert.throws(() => register('T04_LAZY', 1, 2, () => Value.text('x'), { lazy: true }), TypeError);
    assert.throws(() => register({ name: 'T04_BINDS', min: 2, max: 2, binds: true, fn: () => Value.text('x') }), TypeError);
    assert.throws(() => register({ name: 'T04_RULE', min: 1, max: 3, arityError: () => null, fn: () => Value.text('x') }), TypeError);
    assert.throws(() => compile('T04_LAZY(1)'), (e) => e.code === 'E_UNKNOWN_FUNC');
  });
  expectOk('register refuses a malformed name and an inverted arity', () => {
    assert.throws(() => register('bad name!', 0, 0, () => Value.text('x')), TypeError);
    assert.throws(() => register('T04_ARX', 3, 1, () => Value.text('x')), RangeError);
    assert.throws(() => register('T04_INF', 0, Infinity, () => Value.text('x')), RangeError);
  });
  expectOk('register: a native return value is a TypeError, never a result', () => {
    register('T04_NUMRET', 0, 0, () => 42);
    assert.throws(() => compile('T04_NUMRET()').run(), TypeError);
  });
  expectOk('register: a missing max is min, and overwrite: false refuses a repeat', () => {
    register({ name: 'T04_ONE', min: 1, fn: (a) => a.val(0) });
    assert.throws(() => compile('T04_ONE(1, 2)'), (e) => e.code === 'E_ARITY');
    assert.throws(() => register({ name: 'T04_ONE', min: 1, overwrite: false, fn: (a) => a.val(0) }), Error);
  });
}

// --- relational edges, regex portability, size caps -------------
{
  const run = (source, input = {}) => {
    try { return compile(source).run(Value.fromNative(input)).dump(); } catch (e) { return `!${e.code ?? e.name}@${e.line}:${e.col}`; }
  };
  const isCode = (name, source, want) => expectOk(name, () => {
    const got = run(source);
    assert.ok(got.startsWith(`!${want}`), `${source.slice(0, 60)} => ${got.slice(0, 80)}`);
  });
  const equal = (name, source, want) => expectOk(name, () => assert.equal(run(source), want));

  // An aggregate visits a snapshot; a body that grows its source neither
  // extends the walk nor invalidates what it stands on.
  equal('MAP over a list its body appends to visits the snapshot', 'A = (1, 2); COUNT(MAP(A, A[COUNT(A) + 1] = 0))', 't"2"');
  equal('FILTER over a record its body adds keys to visits the snapshot', 'R = RECORD("a", 1); R["b"] = 2; COUNT(FILTER(R, (R[_K & "x"] = 1; TRUE)))', 't"2"');
  equal('TOP_BY over a list its key body appends to visits the snapshot', 'A = (1, 2); COUNT(TOP_BY(A, (A[COUNT(A) + 1] = 0; _), 5))', 't"2"');
  equal('an overwritten later element is still visited as it was', 'A = (1, 2, 3); SUM(A, (A[3] = 100; _))', 't"6"');
  // One total order, by kind then by value.
  equal('SORT ranks NULL < BOOL < numbers < text < BIN and is stable',
    'LIST("10", "9", "1a", "", " 2", "-0", "1e3", "007", "7", "0", FROM_HEX("00ff"), TRUE, FALSE, NULL) .> SORT() .> MAP(IF(_ EQL NULL, "N", IF(_ EQL FALSE, "F", IF(_ EQL TRUE, "T", IF(_ EQL FROM_HEX("00ff"), "B", IF(ISNUM(_), "n" & _, "s" & _)))))) .> JOIN(",")',
    't"N,F,T,n-0,n0,n007,n7,n9,n10,s,s 2,s1a,s1e3,B"');
  equal('numeric-looking text sorts by value before other text', 'LIST("10", "9", "1a") .> SORT() .> JOIN(",")', 't"9,10,1a"');
  equal('SORT_DESC reverses the ranks and keeps ties in input order', 'LIST("7", "007", "1a") .> SORT_DESC() .> JOIN(",")', 't"1a,7,007"');
  // The direction and the count are evaluated and checked even on nothing.
  isCode('SORT_BY validates its direction on an empty list', 'SORT_BY(LIST(), _ + 0, "X")', 'E_BAD_ARG');
  isCode('SORT_BY validates its direction on NULL', 'SORT_BY(NULL, _, "UP")', 'E_BAD_ARG');
  isCode('TOP_BY validates its direction on an empty list', 'TOP_BY(LIST(), _, "UP", 1)', 'E_BAD_ARG');
  // SPEC 7.3: BUCKET.
  equal('BUCKET of a scalar makes one group (bare)', 'COUNT(BUCKET("abc", _))', 't"1"');
  equal('BUCKET of a scalar makes one group (projected)', 'COUNT(BUCKET("abc", _, _))', 't"1"');
  equal('BUCKET of a scalar keeps the scalar as the member', 'BUCKET(5, _, _)["1"]["1"]', 't"5"');
  equal('two keys with one text are one group and lose no row',
    'A = "x"; A["k"] = 1; R = LIST(A, "x", A) .> BUCKET(_); JOIN(LIST(COUNT(R), COUNT(R["x"])), ",")', 't"1,3"');
  // Same-named binders — the right shadows the left, both paths agree.
  expectOk('a join on one binder name gives the general path\'s answer', () => {
    const fast = run('T = LIST(RECORD("id", 1, "mgr", 1), RECORD("id", 2, "mgr", 1)); COUNT(LINK(T, T, T["id"] == T["mgr"]))');
    const general = run('T = LIST(RECORD("id", 1, "mgr", 1), RECORD("id", 2, "mgr", 1)); COUNT(LINK(T, T, T["id"] == T["mgr"] AND TRUE))');
    assert.equal(fast, general);
  });
  // A comma-list operand reading both binders is not one-sided.
  expectOk('a comma list in a join key is classified by what it reads', () => {
    const src = (extra) => `A = LIST(RECORD("k", 1)); B = LIST(RECORD("k", 1)); COUNT(LINK(A, B, (1, _1["k"]) == (1, _2["k"])${extra}))`;
    assert.equal(run(src('')), run(src(' AND TRUE')));
  });
  // Cost, not answers.
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

  // --- regex ---
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
  accept('[.[] literal dot and bracket (the bracket is not followed by : . =)', '^[.[]$', '[');
  reject('unterminated POSIX prefixes are refused too', '[[.]');
  reject('unterminated POSIX prefix, equals form', '[a[=]');
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

  // --- size caps ---
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

  // --- the static exponential-ambiguity rule (SPEC 7.8) -----------
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

// --- host API contract (spec/SPEC.md §8): flow-sensitive dependencies(),
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
    expectOk(`dependencies: ${src} => ${want}`, () => assert.equal(deps(src), want));
  }
  expectOk('dependencies keeps its E_DEPTH cap and position', () => {
    const src = 'A' + '+A'.repeat(300);
    const e = (() => { try { deps(src); } catch (x) { return x; } return null; })();
    assert.ok(e && e.code === 'E_DEPTH', 'want E_DEPTH');
  });
  for (const bad of [12, null, undefined, {}, ['1'], 1n, Symbol.iterator, Buffer.from('1')]) {
    expectCode(`compile(${typeof bad === 'symbol' ? 'symbol' : String(bad)}) is E_BAD_ARG`, 'E_BAD_ARG', () => compile(bad));
  }
  registerFunction('T12_OOB', 1, 3, (a) => a.text(2));
  registerFunction('T12_OOB_VAL', 1, 3, (a) => a.val(7));
  registerFunction('T12_OOB_POS', 1, 3, (a) => { a.posOf(-1); return Value.text('x'); });
  registerFunction('T12_OOB_SYM', 1, 3, (a) => { a.symbol(4); return Value.text('x'); });
  for (const src of ['T12_OOB("x")', 'T12_OOB_VAL("x")', 'T12_OOB_POS("x")', 'T12_OOB_SYM("x")']) {
    expectOk(`host argument read past the count: ${src}`, () => {
      let e = null;
      try { compile(src).run(Value.none()); } catch (x) { e = x; }
      assert.ok(e && e.code === 'E_BAD_ARG', `want E_BAD_ARG, got ${e && (e.code || e.name)}`);
      assert.equal(e.line, 1);          // positioned at the call
    });
  }
  expectOk('a host function may still read an argument the call does have', () => {
    registerFunction('T12_OK', 1, 2, (a) => Value.text(a.count() > 1 ? a.text(1) : a.text(0)));
    assert.equal(compile('T12_OK("a", "b")').run(Value.none()).scalar, 'b');
    assert.equal(compile('T12_OK("a")').run(Value.none()).scalar, 'a');
  });
  // The command line: one plain line on stderr, no stack trace, a small non-zero status.
  const cli = resolve('js/bin/sel.mjs');
  const missing = join(tmpdir(), 'sel-no-such-dir', 'no-such-file.sel');
  for (const [label, argv] of [['-e without operand', ['-e']], ['--deps -e without operand', ['--deps', '-e']],
    ['a missing file', [missing]], ['an unknown option', ['--no-such-flag']]]) {
    expectOk(`CLI misuse: ${label}`, () => {
      const r = spawnSync(process.execPath, [cli, ...argv], { encoding: 'utf8', input: '' });
      assert.ok(r.status > 0 && r.status < 128, `status ${r.status}`);
      assert.equal(r.stdout, '');
      assert.ok(r.stderr.trim().split('\n').length === 1, `not one line: ${r.stderr}`);
      assert.ok(!/node:|file:\/\/\/|Error:|at .*\(/.test(r.stderr), `stack trace: ${r.stderr}`);
    });
  }
}

// --- an application's function is never assumed harmless (spec/SPEC.md §8.1):
// however it was installed, a call to it may write, so no copy is put off past
// it and no aliasing outlives it (registry.mayHaveEffects) -----------------
{
  const { registerFunction } = await import('../js/src/sel.mjs');
  const { define, mayHaveEffects, hostArity } = await import('../js/src/registry.mjs');
  const { mayWrite } = await import('../js/src/ast.mjs');
  let installs = 0;
  // POKE rewrites X[1]["k"] from 1 to 9 -- from its FROM-th call on -- and
  // answers ANSWER. INSTALL defines it under a fresh name (the table cannot
  // drop one) and answers the call as a program spells it.
  const poke = (install, src, { from = 1, answer = Value.text('1') } = {}) => {
    const ctx = Value.fromNative({ X: [{ k: 1 }, { k: 2 }], L: [{ a: 1 }, { a: 2 }] });
    const x1 = ctx.get('X').get('1');
    let calls = 0;
    const fn = () => {
      calls += 1;
      if (calls >= from) x1.set('k', Value.int(9));
      return answer;
    };
    installs += 1;
    return compile(src.replaceAll('POKE', install(`T13_POKE_${installs}`, fn))).run(ctx).scalar;
  };
  const installers = {
    'a registered function': (name, fn) => { registerFunction(name, 0, 0, fn); return `${name}()`; },
    'a replaced registration': (name, fn) => {
      registerFunction(name, 0, 0, () => Value.text('1'));
      registerFunction(name, 0, 0, fn);
      return `${name}()`;
    },
    'a define()d strict function': (name, fn) => { define({ name, min: 0, max: 0, fn }); return `${name}()`; },
    'a define()d lazy function': (name, fn) => {
      define({ name, min: 0, max: 0, lazy: true, fn });
      return `${name}()`;
    },
    // examples/fn-complex's form: a binding function defined in place.
    'a define()d binding function': (name, fn) => {
      define({ name, min: 2, max: 3, lazy: true, binds: true, fn });
      return `${name}(LIST(1), _)`;
    },
  };
  for (const [what, install] of Object.entries(installers)) {
    // FILTER collected X[1] before POKE ran: the write must not reach it (SPEC 3.4).
    expectOk(`${what}: FILTER(X, TRUE)[POKE]["k"] is 1`, () =>
      assert.equal(poke(install, 'FILTER(X, TRUE)[POKE]["k"]'), '1'));
    // A key that may write copies each element once its key is computed: the
    // second key's write must not reach the first element.
    for (const src of ['SORT_BY(X, POKE)[1]["k"]', 'TOP_BY(X, POKE, 2)[1]["k"]', 'BUCKET(X, POKE)["1"][1]["k"]']) {
      expectOk(`${what}: ${src} is 1`, () => assert.equal(poke(install, src, { from: 2 }), '1'));
    }
    // A predicate that may write re-aliases the right rows per pair: the second
    // left row meets X[1] as the first pair's predicate left it.
    expectOk(`${what}: LINK(L, X, A, B, POKE)[3]["k"] is 9`, () =>
      assert.equal(poke(install, 'LINK(L, X, A, B, POKE)[3]["k"]', { answer: Value.bool(true) }), '9'));
  }
  expectOk('only a shipped builtin is assumed to have no effects', () => {
    assert.equal(mayHaveEffects('FILTER'), false);
    assert.equal(mayHaveEffects('is_null'), false);
    assert.equal(mayHaveEffects('T13_NOT_DEFINED_ANYWHERE'), true);
    registerFunction('T13_HOST_FN', 0, 0, () => Value.text('1'));
    assert.equal(mayHaveEffects('T13_HOST_FN'), true);
    assert.deepEqual(hostArity('T13_HOST_FN'), [0, 0]);
    define({ name: 'T13_LOW_FN', min: 0, max: 0, fn: () => Value.text('1') });
    // Not the application's registration (no SQL arity), but no less able to write.
    assert.equal(mayHaveEffects('T13_LOW_FN'), true);
    assert.equal(hostArity('T13_LOW_FN'), null);
    // A body of shipped calls stays write-free: its copies are still put off
    // to the end, and the right rows still aliased once.
    assert.equal(mayWrite(parse('SORT_BY(X, LOWER(_["k"]))')), false);
    assert.equal(mayWrite(parse('LINK(L, X, A, B, IS_NULL(B["k"]))')), false);
    assert.equal(mayWrite(parse('SORT_BY(X, T13_LOW_FN())')), true);
  });
}

// --- performance work, part 2: the optimisations are invisible ---------
{
  const run = (src, ctx = {}) => compile(src).run(Value.fromNative(ctx));
  const dump = (v) => v.dump();
  // A math plan over an expression with an IF/COND/`,`/`;`/assignment operand answers as the plain tree does.
  const plainEval = (src, ctx = {}) => { const prog = compile(src); return evalNode(prog.ast, new Context(Value.fromNative(ctx))); };
  for (const src of [
    'A * 3 + IF(B > 0, C, 2) - A / 4',
    '(A + 1) * COND(B > 1, 5, B < 0, 7, 9) + C',
    'A + (A = 10; A * 2) + A',
    'A * (1, 2)',
    'N = 1; N + (N = 5) + N',
    'A + IF(TRUE, 1, 1 / 0) * B',
    '1 + IF(B > 0, "x", 2)',
  ]) {
    expectOk(`P11 plan vs plain tree: ${src}`, () => {
      const ctx = { A: 7, B: 1, C: 9 };
      let a, b;
      try { a = dump(run(src, ctx)); } catch (e) { a = `!${e.code}@${e.line}:${e.col}`; }
      try { b = dump(plainEval(src, ctx)); } catch (e) { b = `!${e.code}@${e.line}:${e.col}`; }
      assert.equal(a, b);
    });
  }
  // The append idiom gives the same list and the same depth error whether or not the second copy is made.
  expectOk('P12 A = (A, x) appends and the stored list is independent of later writes', () => {
    const r = run('A = (1, 2); A = (A, 3); B = A; B[1] = 9; (A[1], B[1], COUNT(A))', {});
    assert.equal(r.get('1').scalar, '1'); assert.equal(r.get('2').scalar, '9'); assert.equal(r.get('3').scalar, '3');
  });
  expectOk('P12 the depth check still fires at the target for a list that nests past the cap', () => {
    let deep = 'A[1]'; for (let i = 0; i < 197; i++) deep += '[1]';
    let e; try { run(`${deep} = 1; A = (A, 2); A = (A, 3)`); } catch (x) { e = x; }
    assert.ok(e === undefined || e.code === 'E_DEPTH', `unexpected ${e && e.code}`);
    let deeper = 'A[1]'; for (let i = 0; i < 199; i++) deeper += '[1]';
    let f; try { run(`${deeper} = 1; A = (A, 2)`); } catch (x) { f = x; }
    assert.ok(f && f.code === 'E_DEPTH', `want E_DEPTH, got ${f && f.code}`);
  });
  // A hybrid continuation shares the caller's unrelated entries but never writes through to them.
  {
    const sqlUrl = pathToFileURL(resolve('js/src/sql/index.mjs')).href;
    const { Sql, Binding } = await import(sqlUrl);
    const bindings = { ORDERS: Binding.relation('orders', 'o', { AMOUNT: Binding.column('amount', 'o', 'NUM') }, null, null) };
    const plan = Sql.planHybrid(compile('ORDERS .> FILTER(_["AMOUNT"] > 1) .> MAP(RECORD("a", _["AMOUNT"] * FACTOR)) .> TAKE(3)'), 'mariadb', bindings);
    const ctx = Value.fromNative({ FACTOR: 2, UNRELATED: [{ v: 1 }, { v: 2 }], ORDERS: [{ AMOUNT: 5 }, { AMOUNT: 7 }] });
    const runner = () => [{ AMOUNT: 5 }, { AMOUNT: 7 }];
    expectOk('P16 executeHybrid leaves the caller context untouched and answers like run()', () => {
      const before = ctx.dump();
      const out = Sql.executeHybrid(plan, runner, ctx);
      assert.equal(ctx.dump(), before);
      assert.equal(out.size(), 2);
    });
    const assigning = Sql.planHybrid(compile('ORDERS .> FILTER(_["AMOUNT"] > 1) .> MAP(RECORD("a", _["AMOUNT"])) .> TAKE(3); UNRELATED[1]["v"] = 99; FACTOR += 1; FACTOR'), 'mariadb', bindings);
    expectOk('P16 a continuation that assigns to context names writes only to its own copy', () => {
      const before = ctx.dump();
      const out = Sql.executeHybrid(assigning, runner, ctx);
      assert.equal(ctx.dump(), before, 'the caller context changed');
      assert.equal(out.scalar, '3');
    });
  }
  // BTL elements are distinct values even though byte decimals are shared.
  expectOk('P17 BTL elements are independent values', () => {
    const r = run('B = BTL(FROM_HEX("0101")); B[1] = 7; (B[1], B[2], COUNT(B))');
    assert.equal(r.get('1').scalar, '7'); assert.equal(r.get('2').scalar, '1'); assert.equal(r.get('3').scalar, '2');
    assert.equal(dump(run('BTL(FROM_HEX("00ff80"))')), dump(run('LIST(0, 255, 128)')));
    assert.equal(run('LTB(BTL(FROM_HEX("00ff80")))').kind, 'BIN');
  });
  expectOk('P17 the first BigInt handed to Value.int does not pay for 10^1000000', () => {
    assert.equal(Value.int(123n).scalar, '123');
    assert.equal(Value.int(-(10n ** 17n)).scalar, '-100000000000000000');
    assert.throws(() => Value.int(10n ** 1000000n), (e) => e.code === 'E_RANGE');
  });
  // The integer-digit cap verdict is exact at the boundary.
  expectCode('P18 POWER far past the cap is E_RANGE', 'E_RANGE', () => compile('POWER(POWER(3, 99999), 21)').run(Value.none()));
  expectOk('P18 a number of exactly the cap in digits is legal, one more is not', () => {
    assert.equal(compile('LEN(REPEAT("9", 1000000) + 0)').run(Value.none()).scalar, '1000000');
    let e; try { compile('REPEAT("9", 1000000) + 1').run(Value.none()); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_RANGE', `want E_RANGE, got ${e && e.code}`);
  });
  // A numeric join still raises E_NOT_NUM at the pair the comparison rejects, from the same node.
  expectOk('P19 numeric join over non-numeric keys raises as the comparison does', () => {
    let e; try { run('COUNT(LINK(L, R, A, B, A["k"] == B["k"]))', { L: [{ k: 7 }], R: [{ k: 'x' }] }); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_NOT_NUM', `want E_NOT_NUM, got ${e && e.code}`);
    assert.equal(run('COUNT(LINK(L, R, A, B, A["k"] == B["k"]))', { L: [{ k: 7 }], R: [{ k: '7.0' }] }).scalar, '1');
    assert.equal(Value.text('abc').tryDecimal(), null);
    assert.equal(Value.text('1.50').tryDecimal().scale, 2);
    assert.equal(Value.bool(true).tryDecimal(), null);
  });
  // The i-flag ASCII refusal fires on every call, cached pattern or not, and flags never share a cached regex.
  expectOk('P20 regex compile memo: refusals repeat, flags stay separate', () => {
    for (let i = 0; i < 3; i++) {
      let e; try { run('RMATCH("é", "é", "i")'); } catch (x) { e = x; }
      assert.ok(e && e.code === 'E_BAD_ARG', `call ${i}: want E_BAD_ARG, got ${e && e.code}`);
    }
    assert.equal(dump(run('RMATCH("abc", "ABC")')), 'FALSE');
    assert.equal(dump(run('RMATCH("abc", "ABC", "i")')), 'TRUE');
    assert.equal(dump(run('RMATCH("abc", "ABC")')), 'FALSE');
    assert.equal(dump(run('RMATCH("é", "é")')), 'TRUE');
  });
}

// --- performance work, part 3: the optimisations are invisible ---------
{
  const run = (src, ctx = {}) => compile(src).run(Value.fromNative(ctx));
  const dump = (v) => v.dump();
  // A pure predicate aliases the right rows once; an assigning one per pair. Both give
  // the same joined rows, and a host-function call keeps the per-pair path.
  expectOk('P22 LINK nested loop: pure predicate == impure twin', () => {
    const L = [{ id: 1, v: 3, k: 1 }, { id: 2, v: 9, k: 2 }, { id: 3, v: 5, k: 1 }];
    const R = [{ rid: 1, v: 4, k: 1 }, { rid: 2, v: 8, k: 2 }, { rid: 3, v: 1, k: 1 }];
    for (const fn of ['LINK', 'LINK_LEFT']) {
      const pure = dump(run(`${fn}(L, R, A, B, A["v"] < B["v"] AND A["k"] != B["k"] + 5)`, { L, R }));
      const twin = dump(run(`${fn}(L, R, A, B, (Z = 1; A["v"] < B["v"] AND A["k"] != B["k"] + 5))`, { L, R }));
      assert.equal(pure, twin);
    }
    assert.equal(run('COUNT(LINK(L, R, A, B, A["v"] < B["v"]))', { L, R }).scalar, '3');
    assert.equal(run('COUNT(LINK_LEFT(L, R, A, B, A["v"] > 100))', { L, R }).scalar, '3');
    // The right side is aliased under its own name for every left row.
    assert.equal(run('COUNT(LINK(L, R, A, B, B["v"] > A["v"] AND A["id"] == B["rid"]))', { L, R }).scalar, '1');
  });
  // A numeric literal on the right of an arithmetic/comparison operator is read as its
  // decimal; scale, sign, error positions and the depth boundary are what they were.
  expectOk('P23 right-hand literal fast path keeps values, scale, errors and depth', () => {
    assert.equal(run('A + 1.50', { A: 2 }).scalar, '3.50');
    assert.equal(run('A * 0', { A: 2 }).scalar, '0');
    assert.equal(run('A - 007', { A: 10 }).scalar, '3');
    assert.equal(dump(run('A == 2.0', { A: 2 })), 'TRUE');
    assert.equal(dump(run('A >= 3', { A: 2 })), 'FALSE');
    assert.equal(run('A / 3', { A: 1 }).scalar, '0.3333333333');
    assert.equal(dump(run('A $== 2', { A: '2' })), 'TRUE');
    assert.equal(run('"a" & 1', {}).scalar, 'a1');
    let e; try { run('A + 1', { A: 'x' }); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_NOT_NUM' && e.line === 1 && e.col === 1, `left operand error, got ${e && e.code}@${e && e.col}`);
    e = null; try { run('A + B + 1', { A: 1 }); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_UNDEF_VAR' && e.col === 5, `left error first, got ${e && e.code}@${e && e.col}`);
    const nest = (n, tail) => '('.repeat(n) + tail + ')'.repeat(n);
    assert.equal(run(nest(99, 'A + 1'), { A: 5 }).scalar, '6');
    e = null; try { run(nest(100, 'A + 1'), { A: 5 }); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_DEPTH' && e.col === 101, `depth boundary, got ${e && e.code}@${e && e.col}`);
  });
  // FromCodePoints round-trips at and around the chunk size, and for the empty and one-element arrays.
  expectOk('P27 fromCodePoints: boundaries of the short path and the chunked one', () => {
    assert.equal(fromCodePoints([]), '');
    assert.equal(fromCodePoints([0x1f600]), '\u{1f600}');
    for (const n of [1, 2, 4095, 4096, 4097, 8191, 8192, 8193, 20000]) {
      let str = '';
      for (let i = 0; i < n; i++) str += i % 5 === 0 ? '\u{1f600}' : i % 3 === 0 ? '\u00e9' : String.fromCharCode(97 + (i % 26));
      assert.equal(fromCodePoints(toCodePoints(str, null)), str, `n=${n}`);
    }
  });
  // A packed list hashes like its keyed twin (the key hashes are tabled now), BIN hashing is indexed, past the table too.
  expectOk('P28 structuralHash: packed list == keyed twin, BIN stable, beyond the key table', () => {
    for (const n of [0, 1, 7, 65536, 65540]) {
      const nums = Array.from({ length: n }, (_, i) => i % 5);
      const packed = Value.fromNative(nums);
      const keyed = Value.fromEntries(nums.map((x, i) => [String(i + 1), Value.fromNative(x)]));
      assert.equal(structuralHash(packed), structuralHash(keyed), `n=${n}`);
    }
    const a = Value.bin(Uint8Array.from([1, 2, 3])), b = Value.bin(Uint8Array.from([1, 2, 3])), c = Value.bin(Uint8Array.from([1, 2, 4]));
    assert.equal(structuralHash(a), structuralHash(b));
    assert.notEqual(structuralHash(a), structuralHash(c));
    assert.equal(run('COUNT(DEDUPE(LIST(LIST(1,2), LIST(1,2), LIST(2,1))))').scalar, '2');
  });
  // The pow10 cache evicts the oldest entries (not everything) and every answer stays exact.
  expectOk('P28 pow10 cache: exact through evictions, alternating large scales', () => {
    const ks = [200000, 400000, 600000, 800000, 900000, 1000000, 200000, 600000, 500000, 600000, 500000, 70, 65, 64, 100, 999999];
    for (const k of ks) assert.ok(DEC.pow10(k) === 10n ** BigInt(k), `10^${k}`);
    for (let i = 0; i < 6; i++) for (const k of [600000, 500000]) assert.ok(DEC.pow10(k) === 10n ** BigInt(k));
    assert.equal(DEC.pow10(3), 1000n);
    for (let k = 65; k < 400; k += 7) assert.ok(DEC.pow10(k) === 10n ** BigInt(k));
  });
  // MAP skips its copy only when the body BUILDS the result (RECORD/LIST/arithmetic);
  // a body that returns something by reference still gets its copy (SPEC 3.4).
  expectOk('MAP copies what it collects unless the body built it', () => {
    // Built results: independent of the source, and of each other.
    const prog = `R = MAP(X, RECORD("a", _["a"])); X[1]["a"] = 9; R[1]["a"] = 7; (R[1]["a"], R[2]["a"], X[1]["a"], X[2]["a"])`;
    assert.equal(dump(run(prog, { X: [{ a: 1 }, { a: 2 }] })), '-{"1"=t"7", "2"=t"2", "3"=t"9", "4"=t"2"}');
    const arith = `R = MAP(X, _ * 2); R[1] = 5; (R[1], R[2], X[1], X[2])`;
    assert.equal(dump(run(arith, { X: [1, 2] })), '-{"1"=t"5", "2"=t"4", "3"=t"1", "4"=t"2"}');
    // By reference: still a copy of the element, not the element.
    const alias = `R = MAP(X, _); X[1]["a"] = 9; R[1]["a"]`;
    assert.equal(dump(run(alias, { X: [{ a: 1 }] })), 't"1"');
    const alias2 = `R = MAP(X, _); R[1]["a"] = 9; X[1]["a"]`;
    assert.equal(dump(run(alias2, { X: [{ a: 1 }] })), 't"1"');
  });
  // Results built without the second validation are the same numbers; negative zero
  // never survives, however it arises.
  expectOk('P24 evaluator results: negative zero is normalised, caps still apply', () => {
    for (const src of ['0 * -1', '-(0)', '-0', 'T = 0; T *= -1; T', 'A - A', 'A % 1 * -1', '0 / -5']) {
      const v = run(src, { A: 3 });
      assert.equal(v.scalar, '0', `${src} printed ${v.scalar}`);
      assert.equal(dump(v), 't"0"', `${src} dumped ${dump(v)}`);
    }
    let e; try { run('A * A', { A: '9'.repeat(600000) }); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_RANGE', `want E_RANGE, got ${e && e.code}`);
  });
}

// --- a FILTER that opens with IS_NULL of a LINK_LEFT's right member: the join
// skips building the joined rows of the right rows that conjunct is FALSE on
// (structure.mjs, rightNullRejects), and nothing else changes (spec §7.4) ----
{
  const { SelError } = await import('../js/src/errors.mjs');
  const { MAX_COLLECTION } = await import('../js/src/budget.mjs');
  // The program's two forms, with an error's position as well as its code: as
  // written, and with the join bound to a helper variable first, where no
  // FILTER sits on a LINK and nothing can be skipped.
  const twoForms = (src, input) => {
    const cut = src.lastIndexOf(' .> FILTER(');
    const head = src.slice(0, cut);
    const tail = src.slice(cut + ' .> FILTER('.length);
    const go = (text, headAt) => {
      try {
        return `ok ${compile(text).run(Value.fromNative(input)).dump()}`;
      } catch (e) {
        if (!(e instanceof SelError)) throw e;
        // An error's column, as (segment, offset): the join, or the FILTER on.
        const filterAt = text.lastIndexOf(`FILTER(${tail}`);
        const at = e.col - 1;
        return `err ${e.code} ${e.line}:${at >= filterAt ? `filter+${at - filterAt}` : `join+${at - headAt}`}`;
      }
    };
    return [go(`${head} .> FILTER(${tail}`, 0), go(`J = ${head}; J .> FILTER(${tail}`, 4)];
  };
  // How many records a run builds, its error swallowed: the joined rows a
  // skipping join never builds are the difference. Every record constructor
  // the join reaches is counted.
  const recordsBuilt = (src, input) => {
    const program = compile(src);
    const root = Value.fromNative(input);
    const saved = ['shapedFromShape', 'fromEntriesOwned', 'fromEntriesPreserveDuplicates'].map((name) => [name, Value[name]]);
    let built = 0;
    for (const [name, fn] of saved) Value[name] = (...a) => { built++; return fn(...a); };
    try {
      program.run(root);
    } catch (e) {
      if (!(e instanceof SelError)) throw e;
    } finally {
      for (const [name, fn] of saved) Value[name] = fn;
    }
    return built;
  };
  const left = 'P .> LINK_LEFT(I, _1["id"] == _2["product_id"])';
  const ctx = {
    P: [{ id: 1, name: 'a' }, { id: 2, name: 'b' }, { id: 3, name: 5 }, { id: 4, name: 'd' }],
    I: [{ id: 10, product_id: 1 }, { id: null, product_id: 1 }, { id: 12, product_id: 3 }, { product_id: 9 }],
  };
  for (const src of [
    `${left} .> FILTER(IS_NULL(_["i"]["id"]))`,
    `${left} .> FILTER(IS_NULL(_["I"]["id"])) .> MAP(_K)`,
    `${left} .> FILTER(IS_NULL(_["_2"]["id"]) AND _K $!= "2")`,
    `${left} .> FILTER(r, IS_NULL(r["i"]["id"]) AND r["p"]["name"] > 1)`,
    `${left} .> FILTER(IS_NULL(_["i"]["id"]) AND _["p"]["name"] $!= "b") .> MAP(_["p"]["id"])`,
    `${left} .> FILTER(IS_NULL(_["i"]["product_id"]))`,
    `${left} .> FILTER(IS_NULL(_["i"]["sku"]))`,
    // the body reads the keys the skipped rows leave gaps in, a step after it renumbers
    `${left} .> FILTER(IS_NULL(_["i"]["id"]) AND _K $!= "3") .> TAKE(5)`,
    // not the right row, binders alike, not the shape: nothing is skipped
    `${left} .> FILTER(IS_NULL(_["p"]["id"]))`,
    `${left} .> FILTER(IS_NULL(_["iI"]["id"]))`,
    'LINK_LEFT(P, I, L, R, L["id"] == R["product_id"]) .> FILTER(IS_NULL(_["I"]["id"]))',
    'LINK_LEFT(P, I, X, x, X["id"] == x["product_id"]) .> FILTER(IS_NULL(_["x"]["id"]))',
    `${left} .> FILTER(NOT IS_NULL(_["i"]["id"]))`,
  ]) {
    expectOk(`LINK_LEFT right-null rejection answers as the helper form: ${src}`, () => {
      const [asWritten, throughAVariable] = twoForms(src, ctx);
      assert.equal(asWritten, throughAVariable);
    });
  }
  // Every right row matches every left row, and has an id: the FILTER drops all
  // 40 x 40 joined rows. The rejection builds none of them, anywhere else every one.
  const P = Array.from({ length: 40 }, () => ({ id: 1 }));
  const I = Array.from({ length: 40 }, () => ({ id: 10, product_id: 1 }));
  const skips = (src) => recordsBuilt(src, { P, I }) < P.length * I.length;
  expectOk('LINK_LEFT right-null rejection: the joined rows are not built', () => {
    assert.ok(skips(`${left} .> FILTER(IS_NULL(_["i"]["id"]))`));
    assert.ok(skips(`${left} .> FILTER(IS_NULL(_["_2"]["id"]) AND _K $!= "2") .> MAP(_K)`));
    assert.ok(skips('LINK_LEFT(P, I, L, R, L["id"] == R["product_id"]) .> FILTER(IS_NULL(_["r"]["id"]))'));
  });
  // (Binders spelled alike are left to the helper-form comparison above: such a
  // join's predicate reads one row twice, and as written it matches nothing.)
  for (const src of [
    // not a right binder key of this join: the left one, a mixed-case name, a
    // relation name under explicit binders
    `${left} .> FILTER(IS_NULL(_["p"]["id"]))`,
    `${left} .> FILTER(IS_NULL(_["iI"]["id"]))`,
    'LINK_LEFT(P, I, L, R, L["id"] == R["product_id"]) .> FILTER(IS_NULL(_["I"]["id"]))',
    // not IS_NULL first, not a literal member or field, not the FILTER's own element
    `${left} .> FILTER(TRUE AND IS_NULL(_["i"]["id"]))`,
    `${left} .> FILTER(NOT IS_NULL(_["i"]["id"]))`,
    `${left} .> FILTER(IS_NULL(_[LOWER("I")]["id"]))`,
    `${left} .> FILTER(IS_NULL(_["i"][LOWER("ID")]))`,
    `${left} .> FILTER(r, IS_NULL(_["i"]["id"]))`,
    // an inner join, and a FILTER handed conjuncts by a join above
    'P .> LINK(I, _1["id"] == _2["product_id"]) .> FILTER(IS_NULL(_["i"]["id"]))',
    `${left} .> FILTER(IS_NULL(_["i"]["id"])) .> LINK(I, L, R, L["p"]["id"] == R["product_id"])`
      + ' .> FILTER(_["p"]["id"] > 0) .> MAP(1)',
  ]) {
    expectOk(`LINK_LEFT right-null rejection is not taken: ${src}`, () => assert.ok(!skips(src)));
  }
  // A FILTER whose own body reads `_K` observes the keys its join gives the
  // rows: nothing below it may renumber them, whatever step follows it -- an
  // inner join's left-row drop and a drop for a FILTER further up included.
  expectOk('a FILTER that reads _K keeps the keys of the rows a join drops for it', () => {
    const data = {
      A: [{ id: 1, x: 0 }, { id: 2, x: 5 }, { id: 3, x: 5 }],
      B: [{ aid: 1 }, { aid: 2 }, { aid: 3 }],
      C: [{ cid: 1 }, { cid: 2 }, { cid: 3 }],
    };
    const ids = (src) => compile(src).run(Value.fromNative(data)).dump();
    const joined = 'A .> LINK(B, _1["id"] == _2["aid"]) .> FILTER(_["a"]["x"] > 1 AND _K $!= "2")';
    assert.equal(ids(`${joined} .> TAKE(2) .> MAP(_["a"]["id"])`), '-{"1"=t"3"}');
    assert.equal(ids(`${joined} .> LINK(C, L, R, L["a"]["id"] == R["cid"]) .> FILTER(_["R"]["cid"] > 0)`
      + ' .> MAP(_["L"]["a"]["id"])'), '-{"1"=t"3"}');
  });
  // The join as written builds every matched row before the FILTER drops it,
  // and raises E_RANGE, at the call, when they are more than MAX_COLLECTION
  // (spec §6.4): a row the rejection never builds still counts, at the same place.
  expectOk('LINK_LEFT right-null rejection holds the real collection limit', () => {
    const side = Math.sqrt(MAX_COLLECTION);
    assert.equal(side * side, MAX_COLLECTION);
    const src = 'P .> LINK_LEFT(I, _1["id"] == _2["k"]) .> FILTER(IS_NULL(_["i"]["id"]))';
    const rows = (n, row) => Array.from({ length: n }, () => row);
    assert.equal(compile(src).run(Value.fromNative({ I: rows(side, { id: 5, k: 1 }), P: rows(side, { id: 1 }) })).dump(), '-');
    let e = null;
    try { compile(src).run(Value.fromNative({ I: rows(side, { id: 5, k: 1 }), P: rows(side + 1, { id: 1 }) })); } catch (x) { e = x; }
    assert.ok(e && e.code === 'E_RANGE', `want E_RANGE, got ${e && e.code}`);
    assert.equal(e.col, src.indexOf('LINK_LEFT') + 1);
  });
}

if (boundary.length) {
  console.error(`JS runtime: ${boundary.length} host-boundary contract(s) broken:\n  ` + boundary.join('\n  '));
  process.exit(1);
}
console.log(`JS runtime: ${checks} checks passed`);
