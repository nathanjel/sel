import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { compile, Value, RecordShape } from '../js/src/sel.mjs';
import { parse } from '../js/src/parser.mjs';
import { evalNode, Context } from '../js/src/eval.mjs';

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
if (boundary.length) {
  console.error(`JS runtime: ${boundary.length} host-boundary contract(s) broken:\n  ` + boundary.join('\n  '));
  process.exit(1);
}
console.log(`JS runtime: ${checks} checks passed`);
