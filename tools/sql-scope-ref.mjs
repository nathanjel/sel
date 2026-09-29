#!/usr/bin/env node
// Reference translator for tools/gen-sql-scope-cases.py: reads one JSON spec on
// stdin {src, dialect?, bindings?, as_?: value|condition|statement|plan|params-of-statement, mode?}
// and prints one JSON value: the SQL, "ERR CODE line:col", or "PLAN class | tables | sql".
import { readFileSync } from 'node:fs';
import { compile, Value } from '../js/src/sel.mjs';
import { Sql, SqlError, Binding } from '../js/src/sql/index.mjs';

function vv(x) {
  if (x !== null && typeof x === 'object') {
    const v = Value.list([]);
    for (const [k, y] of Object.entries(x)) v.set(k, vv(y));
    return v;
  }
  return Value.text(String(x));
}
function mk(b) {
  const kind = b.kind || 'column';
  if (kind === 'column') return Binding.column(b.column, b.table ?? null, b.type ?? 'UNKNOWN');
  if (kind === 'columns') return Binding.columns(...b.items.map(mk));
  if (kind === 'relation') {
    const f = {};
    for (const [k, v] of Object.entries(b.fields || {})) f[k] = mk(v);
    return Binding.relation(b.from, b.alias ?? null, f, b.scalar ?? null);
  }
  if (kind === 'value') return Binding.value(vv(b.value), b.type ?? null);
  throw new Error('binding kind ' + kind);
}
const spec = JSON.parse(readFileSync(0, 'utf8'));
const bind = {};
for (const [k, v] of Object.entries(spec.bindings || {})) bind[k] = mk(v);
const dialect = spec.dialect || 'mariadb';
const as = spec.as_ || 'value';
const mode = spec.mode || 'inline';
let out;
try {
  const p = compile(spec.src);
  if (as === 'plan') {
    const pl = Sql.planHybrid(p, dialect, bind, {});
    const c = pl.pureSql ? 'pure_sql' : pl.pureMemory ? 'pure_memory' : 'hybrid';
    out = `PLAN ${c} | ${JSON.stringify(pl.sourceTables)} | ${pl.sqlStatement ? pl.sqlStatement.asStatement(mode) : ''}`;
  } else if (as === 'params-of-statement') {
    const f = Sql.translateStatement(p, dialect, bind, {});
    f.asStatement('params');
    out = f.bindings().map((v) => v.dump()).join(', ');
  } else {
    const f = as === 'statement' ? Sql.translateStatement(p, dialect, bind, {}) : Sql.translate(p, dialect, bind, {});
    out = as === 'condition' ? f.asCondition(mode) : as === 'statement' ? f.asStatement(mode) : f.asValue(mode);
  }
} catch (e) {
  if (e instanceof SqlError) out = `ERR ${e.code} ${e.line}:${e.col}`;
  else out = 'HOST ' + e.constructor.name + ': ' + String(e.message).slice(0, 80);
}
process.stdout.write(JSON.stringify(out));
