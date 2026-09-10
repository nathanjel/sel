#!/usr/bin/env node
// Run sql/cases/*.sqlt against the JS translator.
//
// The counterpart of php/bin/sqlt, and deliberately not all of it, on the same
// split python/bin/sqlt draws. That runner has five checks; three ask about the
// HOST and are here, two ask about the MAP and the SUITE and are not:
//
//   here   runCase           the emitted string, the error code and position, the
//                            parameter list — what this host does with shared data
//   here   the mirror        re-run every mariadb case as mysql, requiring the same
//                            string: does THIS host emit identical SQL for a leaf
//                            that overrides nothing
//   here   the slot checks   every bound value is emitted, and bindings() agrees in
//                            count with the placeholders
//
//   not    caveat pins       every caveated entry is pinned by some case. A property
//                            of the case files against the dialect data, both shared
//                            and both already checked by PHP.
//   not    mirror parity     mariadb and mysql declare the same map. Likewise a
//                            property of sql/dialects/*.json and nothing to do with
//                            which host reads it.
//
// That split is why js/src/sql/map.mjs has no trace facility: nothing here needs
// one, and adding one would only be to re-run a check PHP already owns.
//
//     node js/bin/sqlt.mjs [name-substring ...]

import { compile } from '../src/sel.mjs';
import { SelError } from '../src/errors.mjs';
import { Sql, SqlError, map as sqlmap } from '../src/sql/index.mjs';
import { SQL_CASES } from './case-data.mjs';

// A malformed suite. Not a failing case — a suite that cannot be run.
class SuiteError extends Error {}

const MIRRORS = { mariadb: 'mysql' };

// `--- throws` names PHP's class, because the cases were written for one host.
// The line it draws is the one that matters and it is the same in every host: a
// malformed map or registration is a mistake in the application's startup, not a
// rule that cannot be translated, so it must NOT be catchable as SqlError —
// tryTranslate() swallows the second and must not swallow the first. PHP spells
// that LogicException, Python RuntimeError; this host spells it a plain Error,
// which is what js/src/registry.mjs already throws for a function defined twice.
//
// Matched on the exact constructor rather than instanceof: every host error is an
// Error here, so `instanceof Error` would accept a TypeError from a genuine bug
// as though the case had asserted it.
//
// A name with no entry here is a suite error rather than a pass: an expectation
// nobody has mapped must never be satisfied by whatever happened to be thrown.
const THROWS = { LogicException: Error };

// A case's runtime registrations, applied before translating.
function applyRegistrations(ops) {
  if (ops === null || ops === undefined) return;
  for (const op of ops) {
    if (Object.hasOwn(op, 'define')) {
      const [dialect, section, key, entry] = op.define;
      sqlmap.define(dialect, section, key, entry);
      continue;
    }
    if (Object.hasOwn(op, 'dialect')) {
      const rest = {};
      for (const [k, v] of Object.entries(op)) if (k !== 'dialect') rest[k] = v;
      sqlmap.defineDialect(op.dialect, rest);
      continue;
    }
    throw new SuiteError('a register op needs a dialect or a define');
  }
}

const ERROR_RE = /^(\S+)(?:\s+(\d+):(\d+))?$/;
const TILDE_RE = /~\d+~/g;

// null when the case passes, else what went wrong.
function runCase(c) {
  const dialect = c.dialect;
  if (dialect === null || dialect === '') {
    throw new SuiteError(`${c.at}: case ${c.name} has no --- dialect`);
  }
  const options = c.options || {};
  const as = c.as || 'value';
  const mode = c.mode || 'inline';

  let sql = null;
  let error = null;
  let thrown = null;
  let frag = null;
  try {
    // Inside the try: a bad registration is one of the outcomes a case may
    // assert, so it has to be catchable rather than fatal.
    applyRegistrations(c.register);
    // Inside the try: a Binding constructor refuses a malformed binding at the
    // earliest possible moment, which is construction rather than translation,
    // and that refusal is one of the outcomes a case asserts.
    const bindings = c.bindings();
    const program = compile(c.source);
    frag = Sql.translate(program, dialect, bindings, options);
    sql = as === 'condition' ? frag.asCondition(mode)
      : as === 'statement' ? frag.asStatement(mode)
      : frag.asValue(mode);
  } catch (e) {
    if (e instanceof SuiteError) throw e;
    else if (e instanceof SqlError) error = e;
    else if (e instanceof SelError) return `the source did not compile: ${e}`;
    else thrown = e;
  }

  // A malformed map or binding is a mistake in the application's startup and
  // throws the host's own error type; a rule that cannot be translated throws
  // SqlError. Keeping the two apart is why `throws` exists as its own outcome
  // rather than as another E_SQL_ code.
  if (c.throws !== null && c.throws !== undefined) {
    const want = Object.hasOwn(THROWS, c.throws) ? THROWS[c.throws] : null;
    if (want === null) {
      throw new SuiteError(`${c.at}: no JS equivalent is recorded for --- throws `
        + `${c.throws}; add one to THROWS`);
    }
    if (thrown === null) {
      return `expected ${c.throws}, got ${error ? String(error) : JSON.stringify(sql)}`;
    }
    if (thrown.constructor === want) return null;
    return `expected ${c.throws} (${want.name}), got ${thrown.constructor.name} (${thrown})`;
  }
  if (thrown !== null) {
    throw new SuiteError(`${c.at}: unexpected ${thrown.constructor.name}: ${thrown.stack}`);
  }

  if (c.error !== null && c.error !== undefined) {
    if (error === null) return `expected ${c.error}, got ${JSON.stringify(sql)}`;
    const m = ERROR_RE.exec(c.error);
    if (m === null) throw new SuiteError(`${c.at}: malformed error expectation`);
    if (error.code !== m[1]) return `expected ${m[1]}, got ${error.code} (${error.message})`;
    if (m[2] !== undefined) {
      const wantPos = `${m[2]}:${m[3]}`;
      const gotPos = `${error.line}:${error.col}`;
      if (wantPos !== gotPos) return `expected ${m[1]} at ${wantPos}, got it at ${gotPos}`;
    }
    return null;
  }

  if (error !== null) return `expected SQL, got ${error.code} (${error.message})`;
  if (sql !== c.expect) return `got:  ${sql}\n     want: ${c.expect}`;

  // Checked for every case that produces a fragment, not only those asking about
  // params: every slot in the part list must have a value, and every value must
  // be emitted. A value bound but never emitted means a Fragment was rendered and
  // thrown away — invisible in `inline` mode, which is what the rest of the suite
  // asserts.
  const seen = new Set();
  for (const p of frag.parts) {
    if (typeof p === 'string') continue;
    if (p < 1 || p > frag.params.length) {
      return `parameter slot ${p} has no value in params`;
    }
    seen.add(p);
  }
  const orphans = [];
  for (let i = 1; i <= frag.params.length; i += 1) if (!seen.has(i)) orphans.push(i);
  if (orphans.length) {
    return `parameter slot(s) ${JSON.stringify(orphans)} were bound but never `
      + 'emitted — a fragment was rendered and discarded';
  }
  // `~1~`, not bare tildes: PostgreSQL's regex operator IS `~`, so counting them
  // divided a regex fragment's odd tilde count by two. PHP's runner shipped that
  // bug; this is the corrected form from the start.
  const debugStr = frag.kind === 'STATEMENT' ? frag.asStatement('debug') : frag.asValue('debug');
  const tildes = debugStr.match(TILDE_RE) || [];
  if (frag.bindings().length !== tildes.length) {
    return 'bindings() and the emitted placeholders disagree in count';
  }

  if (c.params !== null && c.params !== undefined) {
    const got = frag.bindings().map((v) => v.dump()).join(', ');
    if (got !== c.params) return `params got:  ${got}\n     want: ${c.params}`;
  }
  return null;
}

function main(argv) {
  const filters = argv.slice(2);
  const cases = SQL_CASES;

  // `--names` prints what this host loaded and stops. Kept as a cheap way to see
  // the roster; the hosts cannot disagree about it any more, because
  // tools/gen-sql-cases.mjs is the only thing that reads sql/cases/*.sqlt and
  // they all load what it wrote.
  if (filters.length === 1 && filters[0] === '--names') {
    for (const c of cases) process.stdout.write(`${c.at}\t${c.name}\n`);
    return 0;
  }

  let passed = 0;
  let mirrored = 0;
  const failures = [];
  let suiteErrors = 0;

  for (const c of cases) {
    if (filters.length && !filters.some((f) => c.name.includes(f))) continue;
    sqlmap.reset();        // no case may leak a registration into another
    let problem;
    try {
      problem = runCase(c);
    } catch (e) {
      if (!(e instanceof SuiteError)) throw e;
      process.stdout.write(`SUITE ERROR ${e.message}\n`);
      suiteErrors += 1;
      continue;
    }
    if (problem !== null) { failures.push([c, problem]); continue; }
    passed += 1;

    // The same case under the mirrored dialect. A registration case is exempt: it
    // names its dialect in the register data, so re-running it under another name
    // would be testing something it does not claim.
    const mirror = Object.hasOwn(MIRRORS, c.dialect) ? MIRRORS[c.dialect] : null;
    if (mirror === null || (c.register !== null && c.register !== undefined)) continue;
    sqlmap.reset();
    // Spread is RIGHT-wins here, where PHP's `+` is left-wins; the assertion
    // below is the same one php/bin/sqlt carries, because getting this backwards
    // re-runs every mariadb case as mariadb and the count line still prints a
    // plausible number.
    const mirrorCase = { ...c, dialect: mirror };
    if (mirrorCase.dialect !== mirror) {
      process.stdout.write(`SUITE ERROR the mirrored case for ${c.name} is still `
        + `${mirrorCase.dialect}, so nothing is being mirrored\n`);
      suiteErrors += 1;
      continue;
    }
    try {
      problem = runCase(mirrorCase);
    } catch (e) {
      if (!(e instanceof SuiteError)) throw e;
      process.stdout.write(`SUITE ERROR (mirrored to ${mirror}) ${e.message}\n`);
      suiteErrors += 1;
      continue;
    }
    if (problem === null) mirrored += 1;
    else {
      failures.push([c, `mirrored to ${mirror}, which must agree with ${c.dialect}: ${problem}`]);
    }
  }

  for (const [c, problem] of failures) {
    process.stdout.write(`FAIL ${c.name}  (${c.at})\n`);
    process.stdout.write(`     ${problem}\n`);
  }

  process.stdout.write(`\n${passed} passed (${mirrored} also checked against a `
    + `mirrored dialect), ${failures.length} failed, ${suiteErrors} suite errors\n`);
  return failures.length === 0 && suiteErrors === 0 ? 0 : 1;
}

process.exit(main(process.argv));
