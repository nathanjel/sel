#!/usr/bin/env node
// Rebuild the whole shipped map through the public registration API, then diff
// what the translator can observe against the map beside it.
//
//   node js/bin/sqlreplay.mjs
//
// sql/MAP.md §4.5¼ states the property this asserts:
//
//   Anything the shipped map contains, an application could have registered.
//
// That is not a nicety. It is what lets a host ship its map as generated CODE
// -- a sequence of defineDialect and define calls run at start-up -- instead of
// carrying a data file the application has to deploy and the host has to parse.
// A host with no JSON reader, which is the point, cannot do the second. So the
// day a map feature appears that only the generator can express, this fails, and
// it fails before somebody has written half a code generator against it.
//
// It replays the documents AS WRITTEN, chain preserved, so no dialect repeats
// what it inherits -- which is also the shape a generator should emit.

import { RAW } from './map-replay.mjs';
import { DIALECTS } from '../src/sql/_map.mjs';
import * as map from '../src/sql/map.mjs';
import { compile } from '../src/sel.mjs';
import { Binding, Sql } from '../src/sql/index.mjs';

// A suffix, because the shipped names are already defined and a name means one
// dialect. The suffix is not a legal dialect name anywhere else, which is the
// point: nothing can confuse the replay with the real thing.
const SUF = '~replay';

let calls = 0;
for (const doc of RAW) {
  map.defineDialect(doc.dialect + SUF, {
    extends: doc.extends == null ? null : doc.extends + SUF,
    version: doc.version,
    target: doc.target ?? true,
    lexical: doc.lexical ?? {},
  });
  calls += 1;
  for (const section of ['ops', 'funcs', 'skel']) {
    for (const [key, entry] of Object.entries(doc[section] ?? {})) {
      map.define(doc.dialect + SUF, section, key, entry);
      calls += 1;
    }
  }
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const problems = [];
let compared = 0;
for (const [name, flat] of Object.entries(DIALECTS)) {
  if (!flat || typeof flat !== 'object' || !('dialect' in flat)) continue;
  if (map.version(name) !== map.version(name + SUF)) {
    problems.push(`${name}: version ${map.version(name)} vs ${map.version(name + SUF)}`);
  }
  compared += 1;
  for (const key of Object.keys(flat.lexical ?? {})) {
    compared += 1;
    if (!same(map.lexical(name, key), map.lexical(name + SUF, key))) {
      problems.push(`${name}.lexical.${key}`);
    }
  }
  for (const section of ['ops', 'funcs', 'skel']) {
    for (const key of Object.keys(flat[section] ?? {})) {
      compared += 1;
      if (!same(map.entry(name, section, key), map.entry(name + SUF, section, key))) {
        problems.push(`${name}.${section}.${key}`);
      }
    }
  }
}

// Rule 10 of sql/MAP.md, at run time. Registering a derived dialect is the
// documented way to adapt the map to a server -- the first external user of this
// layer did it on their first day -- and numericGuard is the one lexical key where
// a wrong override fails SILENTLY: every other one blows up at template expansion,
// and this one emits SQL that answers where SEL refuses. The generator has
// enforced the rule since the guard existed; nothing enforced it for a dialect the
// generator never sees.
//
// End to end rather than by calling the check directly, because the check being
// right is worth nothing if the emit path does not reach it. The bad guard accepts
// integers only, so it lets through the '2.5' that ISNUM's pattern catches: a
// guard that asks a narrower question passes what it should have stopped.
const guardName = `mariadb${SUF}~guard`;
map.defineDialect(guardName, {
  extends: `mariadb${SUF}`,
  lexical: { numericGuard: "CASE WHEN ({0} REGEXP '\\\\A-?[0-9]+\\\\z') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END" },
});
let refused = false;
try {
  Sql.translate(compile('T == 25'), guardName, { T: Binding.column('t', null, 'TEXT') });
} catch (e) {
  if (e && e.code) {
    problems.push(`numericGuard disagreeing with ISNUM raised ${e.code} rather than `
      + 'a registration error');
  } else {
    refused = true;
  }
}
if (!refused) {
  problems.push('a numericGuard that disagrees with its funcs.ISNUM was accepted; '
    + 'sql/MAP.md rule 10 holds at generation time and not at run time');
}

for (const p of problems) process.stdout.write(`  DIFFERS ${p}\n`);
process.stdout.write(`${calls} registration calls rebuilt the map, `
  + `${compared} lookups compared, ${problems.length} differences`
  + ' (and a disagreeing numericGuard is refused)\n');
process.exit(problems.length === 0 ? 0 : 1);
