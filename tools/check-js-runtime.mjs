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
console.log(`JS runtime: ${checks} checks passed`);
