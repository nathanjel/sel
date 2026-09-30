// JS-P26: the dialect chain was rebuilt on every lexical()/entry() lookup.
import { bench, rng, root } from './lib.mjs';
import { pathToFileURL } from 'node:url';
import { resolve, dirname } from 'node:path';
const base = process.env.SEL_JS_ENTRY ? dirname(process.env.SEL_JS_ENTRY) : resolve(root, 'js/src');
const map = await import(pathToFileURL(resolve(base, 'sql/map.mjs')).href);
const emit = await import(pathToFileURL(resolve(base, 'sql/emit.mjs')).href);
const { Sql, Binding } = await import(pathToFileURL(resolve(base, 'sql/index.mjs')).href);
const { compile } = await import(pathToFileURL(resolve(base, 'sel.mjs')).href);
const N = 1000000;
bench(`P26 lexical() x${N} (mariadb, depth 3 chain)`, () => { let n = 0; for (let i = 0; i < N; i++) n += String(map.lexical('mariadb', 'textQuote')).length; return n; }, { reps: 5 });
bench(`P26 entry() x${N} (mariadb funcs.UPPER)`, () => { let n = 0; for (let i = 0; i < N; i++) n += map.entry('mariadb', 'funcs', 'UPPER') === map.MISSING ? 0 : 1; return n; }, { reps: 5 });
const src = 'ALL(ITEMS, I, I["QTY"] > 0 AND LEFT(I["SKU"], 3) $== "GM-" AND I["PRICE"] * 2 < 100) AND ANY(ITEMS, J, UPPER(J["SKU"]) $== "AB") AND COUNT(ITEMS) >= 3';
const b = { ITEMS: Binding.relation('order_items', 'i', { QTY: Binding.column('qty', 'i', 'NUM'), SKU: Binding.column('sku', 'i', 'TEXT'), PRICE: Binding.column('price', 'i', 'NUM') }, null, 'i.order_id = o.id') };
const prog = compile(src);
bench('P26 translate a 30-clause rule x2000 (mariadb)', () => { let n = 0; for (let i = 0; i < 2000; i++) n += String(Sql.translate(prog, 'mariadb', b).sql ?? Sql.translate(prog, 'mariadb', b)).length; return n; }, { reps: 3, warm: 1 });
