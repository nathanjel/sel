// node --expose-gc tools/metadata/js.mjs [bench]; optional SEL_METADATA_ROOT.
import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = process.env.SEL_METADATA_ROOT ?? fileURLToPath(new URL('../../', import.meta.url));
const { Value, compile } = await import(pathToFileURL(`${root}/js/src/sel.mjs`));
const { structuralHash } = await import(pathToFileURL(`${root}/js/src/value.mjs`));
const D = await import(pathToFileURL(`${root}/js/src/decimal.mjs`));
const record = (keys, values) => Value.fromEntries(keys.map((key, i) => [key, values[i]]));
const leaf = Value.text('1'), ctx = Value.none();
ctx.set('X', leaf); ctx.set('Y', Value.text('two'));
const program = compile('RECORD("id", X, "name", Y)');
program.run(ctx);
function churn(n) {
  for (let i=0;i<n;i++) record(['field_'+i,'id'],[leaf,leaf]);
}
function aliases(n) {
  const join=compile('LINK(L, R, O, C, O["id"] == C["id"])'), context=Value.none();
  context.set('R',Value.list([record(['id'],[leaf])]));
  for(let i=0;i<n;i++) {
    context.set('L',Value.list([record(['id','alias_'+i],[leaf,leaf])]));
    join.run(context);
  }
}
if(process.argv[2]==='bench') {
  assert.ok(global.gc,'run with --expose-gc');
  const samples=[];
  for(let r=0;r<5;r++) {
    const start=performance.now();
    for(let i=0;i<20000;i++) program.run(ctx);
    samples.push((performance.now()-start)*1000/20000);
  }
  const powerSamples={};
  for(const exponent of [100,10000]) {
    D.pow10(exponent); powerSamples[exponent]=[];
    for(let r=0;r<5;r++) {
      const tick=performance.now();
      for(let i=0;i<1000;i++) D.pow10(exponent);
      powerSamples[exponent].push((performance.now()-tick)*1000/1000);
    }
  }
  global.gc(); const start=process.memoryUsage().heapUsed;
  churn(20000); global.gc(); const shapes=process.memoryUsage().heapUsed-start;
  aliases(5000); global.gc(); const both=process.memoryUsage().heapUsed-start;
  const beforePowers=process.memoryUsage().heapUsed;
  for(let n=10000;n<12000;n++) D.pow10(n);
  global.gc();
  console.log(JSON.stringify({runtime:process.version,steady_us:samples,
    shapes_retained:shapes,shapes_aliases_retained:both,
    powers_retained:process.memoryUsage().heapUsed-beforePowers,power_us:powerSamples}));
} else {
  const source=[leaf], publicRow=Value.shaped(['public'],source);
  source[0]=Value.text('changed');
  assert.equal(publicRow.get('public').scalar,'1');
  const held=record(['held'],[leaf]), prepared=program.run(ctx).shape;
  churn(2000);
  const again=record(['held'],[leaf]);
  assert.notEqual(held.shape,again.shape); assert.ok(held.eql(again));
  assert.equal(structuralHash(held),structuralHash(again));
  assert.equal(program.run(ctx).shape,prepared);
  for(const keys of [Array.from({length:257},(_,i)=>'wide_'+i),['x'.repeat(16385)]]) {
    assert.notEqual(record(keys,keys.map(()=>leaf)).shape,record(keys,keys.map(()=>leaf)).shape);
  }
  const dynamic=compile('RECORD(K, X)');
  ctx.set('K',Value.text('first')); assert.equal(dynamic.run(ctx).get('first').scalar,'1');
  ctx.set('K',Value.text('second')); assert.equal(dynamic.run(ctx).get('second').scalar,'1');
  assert.equal(compile('RECORD("id", X, "id", Y)').run(ctx).get('id').scalar,'two');
  aliases(1000);
  for(let n=65;n<300;n++) assert.equal(D.pow10(n),10n**BigInt(n));
  assert.equal(D.pow10(10000),10n**10000n);
  for(let n=100000;n<400000;n+=20000) assert.equal(D.pow10(n),10n**BigInt(n));
  assert.equal(D.pow10(1000001),10n**1000001n);
  console.log('JS metadata checks passed');
}
