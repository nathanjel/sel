// SEL → SQL translation.
//
// The design is in `docs/SQL-TRANSLATION.md`; the dialect map format is normative
// in `sql/MAP.md` and the error codes in `sql/errors.md`. `_map.mjs` is generated
// from `sql/dialects/*.json` by `tools/gen-sql-map.mjs` and is the same data every
// host consumes.
//
// The contract this host is graded against is `sql/cases/*.sqlt`, run by
// `js/bin/sqlt`: the cases assert an exact string, so the hosts agreeing on all
// 368 is a measurement rather than an intention.

import * as map from './map.mjs';
import { DIALECTS } from './_map.mjs';
import { Binding } from './binding.mjs';
import { Bindings } from './bindings.mjs';
import { SqlError } from './errors.mjs';
import { Fragment } from './fragment.mjs';
import { RelationalPlan } from './relational-plan.mjs';
import { Translator } from './translator.mjs';

export { DIALECTS, Binding, Bindings, Fragment, RelationalPlan, SqlError, map };

// The public interface of the SQL layer. See docs/SQL-TRANSLATION.md §10.
export class Sql {
  // Translate a compiled program into a SQL expression for one dialect.
  //
  // Throws SqlError, whose message is written to be read. Use this when you want
  // to know why a rule cannot be pushed down: during development, in a build-time
  // audit of a rule set, or in a test.
  static translate(program, dialect, bindings = null, options = null) {
    const t = new Translator(dialect, new Bindings(bindings ?? {}), options ?? {});
    return t.translate(program.ast);
  }

  // The same, returning null instead of throwing.
  //
  // Refusal is an expected, ordinary outcome — "this rule cannot be pushed down,
  // evaluate it here instead" — and an expected outcome should not need a
  // try/catch to observe. Only SqlError is caught: a bug in the translator must
  // not be swallowed by the path that exists to handle refusals.
  static tryTranslate(program, dialect, bindings = null, options = null) {
    try {
      return Sql.translate(program, dialect, bindings, options);
    } catch (e) {
      if (e instanceof SqlError) return null;
      throw e;
    }
  }

  // Translate a relational pipeline program into a SQL statement fragment.
  static translateStatement(program, dialect, bindings = null, options = null) {
    const t = new Translator(dialect, new Bindings(bindings ?? {}), options ?? {});
    return t.translateStatement(program.ast);
  }

  // The same, returning null instead of throwing.
  static tryTranslateStatement(program, dialect, bindings = null, options = null) {
    try {
      return Sql.translateStatement(program, dialect, bindings, options);
    } catch (e) {
      if (e instanceof SqlError) return null;
      throw e;
    }
  }

  // Every dialect that may be named in a translate() call.
  static dialects() {
    return map.targets();
  }
}
