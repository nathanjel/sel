const B = JSON.stringify({
  R: { kind: 'relation', from: 'r', alias: 'r', fields: { id: { column: 'id', type: 'NUM' }, rv: { column: 'rv', type: 'NUM' } } },
  S: { kind: 'relation', from: 's', alias: 's', fields: { id: { column: 'id', type: 'NUM' }, sv: { column: 'sv', type: 'NUM' } } },
  T: { kind: 'relation', from: 't', alias: 't', fields: { tid: { column: 'tid', type: 'NUM' }, tv: { column: 'tv', type: 'NUM' } } },
});
const J1 = 'R .> LINK(S, X, Y, X["id"] == Y["id"])';
const J1L = 'R .> LINK_LEFT(S, X, Y, X["id"] == Y["id"])';
const M = ' .> MAP(RECORD("v", _["tv"]))';
export const P = {
  'p01-reported': `${J1} .> LINK(T, A, B, A["id"] == B["tid"])${M}`,
  'p02-left-only-field': `${J1} .> LINK(T, A, B, A["rv"] == B["tid"])${M}`,
  'p03-right-only-field': `${J1} .> LINK(T, A, B, A["sv"] == B["tid"])${M}`,
  'p04-nested-left': `${J1} .> LINK(T, A, B, A["X"]["id"] == B["tid"])${M}`,
  'p05-nested-right': `${J1} .> LINK(T, A, B, A["Y"]["id"] == B["tid"])${M}`,
  'p06-source-name-binder': `${J1} .> LINK(T, A, B, R["id"] == B["tid"])${M}`,
  'p07-stale-first-binder': `${J1} .> LINK(T, A, B, X["id"] == B["tid"])${M}`,
  'p08-underscore1': `${J1} .> LINK(T, _1["id"] == _2["tid"])${M}`,
  'p09-left-join-chain': `${J1L} .> LINK(T, A, B, A["id"] == B["tid"])${M}`,
  'p10-second-left-join': `${J1} .> LINK_LEFT(T, A, B, A["id"] == B["tid"])${M}`,
  'p11-map-ambiguous-after-chain': `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> MAP(RECORD("v", _["id"]))`,
  'p12-map-nested-after-chain': `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> MAP(RECORD("v", _["A"]["rv"]))`,
  'p13-filter-after-chain': `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> FILTER(_["id"] > 0)${M}`,
  'p14-right-binder-reused-name': `${J1} .> LINK(T, Y, B, Y["id"] == B["tid"])${M}`,
  'p15-left-binder-reused-name': `${J1} .> LINK(T, X, B, X["id"] == B["tid"])${M}`,
  'p16-single-join-control': `R .> LINK(S, X, Y, X["id"] == Y["id"])  .> MAP(RECORD("v", _["sv"]))`,
  "p17-left-join-right-only-field": `${J1L} .> MAP(RECORD("v", _["sv"]))`,
  "p18-left-join-nested-right": `${J1L} .> MAP(RECORD("v", _["Y"]["sv"]))`,
  "p19-three-way-no-clash": `R .> LINK(T, X, Y, X["rv"] == Y["tid"]) .> LINK(S, A, B, A["id"] == B["id"]) .> MAP(RECORD("v", _["sv"]))`,
  "p20-map-nested-ambiguous": `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> MAP(RECORD("v", _["A"]["id"]))`,
  "p21-map-nested-right-only": `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> MAP(RECORD("v", _["A"]["sv"]))`,
  "p22-map-source-name-after-chain": `${J1} .> LINK(T, A, B, A["rv"] == B["tid"]) .> MAP(RECORD("v", _["R"]["id"]))`,
  "p23-single-5arg-map-source-name": `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> MAP(RECORD("v", _["R"]["rv"]))`,
  "p24-single-5arg-map-right-name": `R .> LINK(S, X, Y, X["id"] == Y["id"]) .> MAP(RECORD("v", _["S"]["sv"]))`,
  "p25-single-5arg-pred-source-name": `R .> LINK(S, X, Y, R["id"] == Y["id"]) .> MAP(RECORD("v", _["sv"]))`,
  "p26-single-5arg-pred-right-name": `R .> LINK(S, X, Y, X["id"] == S["id"]) .> MAP(RECORD("v", _["sv"]))`,
  "p27-single-3arg-map-source-name": `R .> LINK(S, _1["id"] == _2["id"]) .> MAP(RECORD("v", _["R"]["rv"]))`,
};
if (process.argv[2] === 'sqlt') {
  for (const [n, s] of Object.entries(P)) process.stdout.write(`### name: zprobe.${n}\n--- dialect\nsqlite\n--- bindings\n${B}\n--- as\nstatement\n--- source\n${s}\n--- expect\n?\n===\n`);
} else {
  const D = 'R = LIST(RECORD("id",1,"rv",1), RECORD("id",2,"rv",2)), S = LIST(RECORD("id",1,"sv",1)), T = LIST(RECORD("tid",1,"tv",7)), ';
  for (const [n, s] of Object.entries(P)) console.log(n + '\t' + D + s);
}
