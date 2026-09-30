// JS-P5/P6: text builtins on a long string, on short rows, and FIND's worst case.
import { sel, bench, growth } from './lib.mjs';
const big = 'abcdefghij'.repeat(100000); // 1M characters, no surrogates
const bigAstral = 'ab😀d'.repeat(250000); // 1M code points with astral characters
const P = (src) => sel.compile(src);
const one = (p, ctx) => () => p.run(ctx).scalar;
for (const [name, src] of [['LEN', 'LEN(S)'], ['LEFT(S,5)', 'LEFT(S, 5)'], ['RIGHT(S,5)', 'RIGHT(S, 5)'], ['SUBSTR(S,500000,5)', 'SUBSTR(S, 500000, 5)'], ['TRIM(S)', 'LEN(TRIM(S))'], ['UPPER(S)', 'LEN(UPPER(S))'], ['FIND(S,"hij")', 'FIND("jab", S, 500000)']]) {
  bench(`P5 ${name} on 1M ascii`, one(P(src), { S: big }), { reps: 5, warm: 1 });
}
bench('P5 LEN on 1M with astral', one(P('LEN(S)'), { S: bigAstral }), { reps: 5, warm: 1 });
bench('P5 FIND on 1M with astral', one(P('FIND("d", S, 500000)'), { S: bigAstral }), { reps: 5, warm: 1 });
bench('P5 LEFT on 1M with astral', one(P('LEFT(S, 5)'), { S: bigAstral }), { reps: 5, warm: 1 });
const rows = Array.from({ length: 300000 }, (_, i) => ({ name: '  name' + i + ' ' }));
bench('P5 MAP(rows, TRIM(name)) 300k rows', one(P('COUNT(MAP(ROWS, TRIM(_["name"])))'), { ROWS: rows }), { reps: 3, warm: 1 });
bench('P5 MAP(rows, LEN/UPPER/LEFT) 300k rows', one(P('COUNT(MAP(ROWS, LEN(UPPER(LEFT(_["name"], 6)))))'), { ROWS: rows }), { reps: 3, warm: 1 });
growth('P5 LEN(S &= "abcdefghij") loop', (n) => { const p = P(`S = ""; L = SPLIT(REPEAT("a,", ${n}), ","); ALL(L, LEN(S &= "abcdefghij") > 0)`); return () => p.run({}).scalar; }, [2500, 5000, 10000], { reps: 3, warm: 1 });
growth('P6 FIND(REPEAT(a,n)&b, REPEAT(a,2n))', (n) => { const p = P(`FIND(REPEAT("a", ${n}) & "b", REPEAT("a", ${2 * n}))`); return () => p.run({}).scalar; }, [5000, 10000, 20000], { reps: 3, warm: 1 });
