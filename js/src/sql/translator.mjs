// Stage 4: render the normalised tree.
//
// Kind inference is folded into this walk rather than run as a separate pass. The
// walk is post-order, so every operand's kind is already known when its parent
// needs it — which is exactly what a separate pass would have computed, at the
// cost of a second traversal and a side table keyed by node identity. Whole-
// expression refusal is unaffected: nothing becomes characters until
// `Fragment.asValue()` is called, so a kind failure still escapes with no partial
// output.

import { toCodePoints } from '../utf8.mjs';
import { validate as regexValidate } from '../builtins/regex.mjs';
import { SelError } from '../errors.mjs';
import { MAX_DEPTH } from '../eval.mjs';
import { asciiUpper } from '../lexer.mjs';
import { Value, quoteDump } from '../value.mjs';
import * as constants from './constants.mjs';
import * as map from './map.mjs';
import * as normalise from './normalise.mjs';
import { Binder } from './binder.mjs';
import { Emit } from './emit.mjs';
import { refuse } from './errors.mjs';
import { Fragment } from './fragment.mjs';

// SEL list keys are the canonical decimals "1", "2", … — so "01" is not a key and
// neither is "1\n", and the evaluator answers E_NO_KEY for both. This layer used
// to answer *element 1* for both, in every host, because `^[0-9]+$` accepts a
// trailing newline (Python's `$`, and PHP's without the D modifier) and
// int()/(int) accept leading zeros. decimal already anchors for exactly this
// reason; the SQL layer did not inherit the lesson.
//
// Nine digits at most, so the conversion is exact in every host that will ever
// implement this: C++'s stoi throws above int32, PHP saturates above int64, JS
// loses precision above 2^53. No list this layer can build has a billion
// elements, so the cap costs nothing and removes the question.
const LIST_KEY = /^[1-9][0-9]{0,8}$/;

// The 1-based position a key names, or null when it names none.
function listKey(k) {
  return LIST_KEY.test(k) ? Number(k) : null;
}

// Lowered by stage 2; none of them is a `funcs` entry. See sql/MAP.md §4.
const AGGREGATES = ['ALL', 'ANY', 'MAP', 'FILTER', 'SUM', 'JOIN'];

const AGG_RETURNS = { ALL: 'BOOL', ANY: 'BOOL', SUM: 'NUM', JOIN: 'TEXT',
  MAP: 'LIST', FILTER: 'LIST' };
const AGG_SKELETON = { ALL: 'all', ANY: 'any', SUM: 'sum', JOIN: 'join' };
const AGG_FOLD = { ALL: 'AND', ANY: 'OR', SUM: '+' };

// The functions whose result has children, so the scalar rule does not apply to
// them. Measured rather than written: every non-lazy name in registry names was
// called and the results with size() > 0 kept. Every one is already refused by
// the dialect documents; the point of the list is that source() used to reach its
// scalar fallback without ever consulting the map, so COUNT and HAS folded to 0
// and FALSE instead.
const YIELDS_LIST = ['BTL', 'INDEXES', 'RGROUPS', 'SPLIT'];

// The functions that read their argument as bytes, and the one that takes a
// BOOL. Both lists were measured rather than written: every name in the registry
// was called with TO_UTF8("a") and with TRUE, and these are the ones SEL did not
// answer E_NOT_* for. Writing them by hand would be the second copy of SEL's
// argument rules that §11.4 exists to avoid — this is a cached measurement, and
// sql/oracle/ re-measures it.
const BIN_ARGUMENT_OK = ['BLEN', 'CRC32', 'ENCODE_BASE64', 'FROM_UTF8', 'ISNUM',
  'TO_HEX', 'TO_UTF8'];
const BOOL_ARGUMENT_OK = ['ISNUM'];

// The runtime kind classes EQL and IN compare, which are not the static kinds. A
// SEL number IS a text value (spec §4), so `1 EQL "1"` is TRUE and NUM and TEXT
// are one class here. BOOL and BIN are each their own: `0 EQL FALSE` is FALSE
// because a number is not a boolean, and `TO_UTF8("a") EQL "a"` is FALSE because
// bytes are not text.
const EQL_CLASS = { NUM: 'text', TEXT: 'text', BOOL: 'bool', BIN: 'bin' };

const NUMERIC_OPS = ['==', '!=', '<', '<=', '>', '>='];
const TEXTUAL_OPS = ['$==', '$!=', '$<', '$<=', '$>', '$>=', 'EQL'];
const BYTE_COMPARISONS = ['$==', '$!=', '$<', '$<=', '$>', '$>=', 'EQL', 'IN'];
const REGEX_AT = { RMATCH: 0, RFIND: 0, RREPLACE: 0, RGROUPS: 0 };

function litNode(t, v, pos) {
  return { t, v, pos };
}

export class Translator {
  constructor(dialect, bindings, options = null) {
    this.dialect = dialect;
    this.emit = new Emit(dialect);
    this.bindings = bindings;
    this.strict = Boolean((options ?? {}).strict ?? false);
    this.params = [];
    this.paramKinds = [];
    // Aggregate binders, innermost last. Consulted before the bindings map, the
    // same precedence Context.lookup gives a binder over a variable, and pushed
    // per element so that nested aggregates shadow independently.
    this.frames = [];
    // A Set, because Python's dict-as-ordered-set is what the caveat list is:
    // insertion order, no duplicates.
    this.caveats = new Set();
    this.constNames = new Map();
    this.constCtx = null;
    // Walk depth, counted exactly as evalNode counts evaluation nesting.
    this.depth = 0;
  }

  translate(ast) {
    map.requireTarget(this.dialect);
    this.bindings.checkAliases();

    this.params = [];
    this.paramKinds = [];
    this.caveats = new Set();
    this.frames = [];
    this.depth = 0;
    [this.constNames, this.constCtx] = constants.scope(this.bindings);
    const f = this.node(normalise.run(ast, this.constNames, this.constCtx));

    return new Fragment(f.parts, f.kind, this.dialect, this.params,
      this.paramKinds, [...this.caveats]);
  }

  // --- the walk ------------------------------------------------------------

  // Ask SEL whether the expression is valid before asking the map whether it is
  // translatable, wherever the arguments are literals and SEL can answer.
  //
  // Only compound nodes are worth checking — a literal cannot be out of range on
  // its own — but *every* one of them, not just the outermost. Checking only the
  // outermost looks like a free optimisation and is not, because SEL is lazy.
  // `FALSE AND (1 / 0 > 0)` is constant and SEL answers FALSE without ever
  // dividing, so validating the AND alone accepts it and `(1 / 0)` goes into the
  // SQL — while `F AND (1 / 0 > 0)`, with a column in place of the FALSE, was
  // refused, because then the AND is not constant and the walk reaches the
  // division on its own. Same division, opposite answer, decided by whether the
  // operand beside it happened to be written down.
  //
  // The dead branch is not currently a wrong answer: MariaDB and PostgreSQL were
  // both asked, and both short-circuit `AND` and `CASE` rather than evaluating
  // the arm they do not take. It is refused because §11.2's first row records
  // that SQL does not promise that, and because a rule that refuses one of two
  // identical divisions is not a rule.
  //
  // **The walk is also bounded, at the evaluator's own limit.** Nothing bounded
  // it, so a flat chain of 201 terms over a column translated — and the evaluator
  // answers E_DEPTH for that same expression. A rule the database answers and SEL
  // does not is the defect above in a different costume, and it was in every host:
  // PHP rendered it, and Python happened to die of its own stack at around 510
  // terms, which is an implementation accident rather than a decision. The guard
  // reads eval's MAX_DEPTH rather than repeating 200, so the two cannot drift.
  node(n) {
    this.depth += 1;
    if (this.depth > MAX_DEPTH) {
      this.depth -= 1;
      refuse('E_SQL_DEPTH',
        `this expression nests deeper than SEL will evaluate (${MAX_DEPTH}), so `
        + 'there is nothing to translate; the evaluator answers E_DEPTH for it', n.pos);
    }
    try {
      if ((n.t !== 'bin' && n.t !== 'un' && n.t !== 'call')
          || !constants.isConstant(n, this.constNames)) {
        return this.dispatch(n);
      }

      const f = this.dispatch(n);
      constants.validate(n, this.constCtx);
      return f;
    } finally {
      this.depth -= 1;
    }
  }

  dispatch(n) {
    const t = n.t;
    if (t === 'num') return this.literal(Value.num(n.v), 'NUM');
    if (t === 'text') return this.literal(Value.text(n.v), 'TEXT');
    if (t === 'bool') return this.literal(Value.bool(n.v), 'BOOL');
    if (t === 'var') return this.variable(n);
    if (t === 'index') return this.index(n);
    if (t === 'un') return this.unary(n);
    if (t === 'bin') return this.binary(n);
    if (t === 'list' || t === 'clist') {
      refuse('E_SQL_SHAPE',
        'a list is not a SQL value; a list can only be the thing an aggregate '
        + 'iterates', n.pos);
    }
    if (t === 'call') return this.call(n);
    refuse('E_SQL_SHAPE', `cannot translate a ${t} node`, n.pos);
  }

  // Every literal becomes a parameter slot; §9 of docs/SQL-TRANSLATION.md.
  //
  // `kind` is both the static kind the expression has and the form the literal is
  // written in, and the two are the same thing only because this is where the AST
  // node kind is still known. A `num` node gives NUM, a `text` node gives TEXT,
  // and no later stage has to guess which of the two a value that happens to read
  // as a number came from.
  literal(v, kind) {
    this.params.push(v);
    this.paramKinds.push(kind === 'UNKNOWN' || kind === 'LIST' ? 'TEXT' : kind);
    return new Fragment([this.params.length], kind, this.dialect);
  }

  variable(n) {
    const bound = this.binder(n.name);
    if (bound !== null) return this.fromBinder(bound, n);
    const b = this.bindings.get(n.name, n.pos);
    const kind = b.kind;
    if (kind === 'column') return this.columnRef(b);
    if (kind === 'value') {
      const v = b.value;
      if (v.size() > 0) {
        refuse('E_SQL_SHAPE',
          `${n.name} is bound to a list, and a list is not a SQL value; it can only `
          + 'be the thing an aggregate iterates', n.pos);
      }
      // An empty binding — {"kind": "value", "value": []}, which is what an empty
      // result set looks like — has no characters, and asking for them raised a
      // SelError from inside asValue(): the wrong class, and thrown past
      // tryTranslate(), which a host uses precisely so it does not need a
      // try/catch. It is legal as the thing an aggregate iterates (ALL over
      // nothing is TRUE) and nothing as a value, so the refusal belongs here,
      // where the walk knows a scalar was asked for.
      if (v.isNone()) {
        refuse('E_SQL_SHAPE',
          `${n.name} is bound to an empty value, which is not a SQL value; only an `
          + 'aggregate can be given an empty binding', n.pos);
      }
      return this.literal(v, declaredKind(b, v));
    }
    if (kind === 'columns' || kind === 'relation') {
      refuse('E_SQL_SHAPE',
        `${n.name} is bound as a ${kind}, which names a set of values rather than `
        + 'one; use it as the first argument of an aggregate, not as a value on its '
        + 'own', n.pos);
    }
    refuse('E_SQL_BINDING', `unusable binding for ${n.name}`, n.pos);
  }

  columnRef(c) {
    const sql = (c.raw ?? null) !== null
      ? String(c.raw)
      : this.emit.column(c.table ?? null, String(c.column));
    return new Fragment([sql], String(c.type || 'UNKNOWN'), this.dialect);
  }

  // Indexing is meaningful against a relation or columns binding — a field or a
  // position — and against nothing else. `A[k]` on a scalar column would have to
  // reach inside a value SQL has no way to look inside.
  index(n) {
    const obj = n.obj;
    if (obj.t !== 'var') {
      refuse('E_SQL_SHAPE',
        'only a bound name can be indexed here; SQL has no way to index into the '
        + 'result of an expression', n.pos);
    }
    const bound = this.binder(obj.name);
    if (bound !== null) {
      return this.indexBinder(bound, obj.name, this.constantIndex(n.idx), n);
    }
    const b = this.bindings.get(obj.name, obj.pos);
    const key = this.constantIndex(n.idx);

    if (b.kind === 'relation') {
      // The binding NAME, not a binder over it. SEL has no row here to index —
      // ITEMS is a list of rows — so it raises E_NO_KEY, and this produced a bare
      // column reference to a table no FROM clause mentions. Inside an aggregate
      // body it was worse than invalid: ALL(ITEMS, I, ITEMS["QTY"] > 0) emitted
      // SQL byte-identical to the binder form and ran and answered, for an
      // expression SEL has no answer for.
      refuse('E_SQL_SHAPE',
        `${obj.name} is a relation, which is a list of rows; indexing it names no `
        + 'value SEL can produce, so use an aggregate and index the row its binder '
        + 'gives you', n.pos);
    }
    if (b.kind === 'columns') {
      const i = listKey(key);
      if (i === null || i > b.items.length) {
        refuse('E_SQL_BINDING',
          `${obj.name}[${key}] is outside that binding's ${b.items.length} column(s)`,
          n.pos);
      }
      return this.columnRef(b.items[i - 1]);
    }
    if (b.kind === 'value') {
      const child = b.value.get(key);
      if (child === undefined || child === null) {
        refuse('E_SQL_BINDING', `${obj.name}["${key}"] is not a key of that value`, n.pos);
      }
      if (child.size() > 0) {
        refuse('E_SQL_SHAPE', `${obj.name}["${key}"] is a list, not a SQL value`, n.pos);
      }
      return this.literal(child, declaredKind(b, child));
    }
    refuse('E_SQL_SHAPE',
      `${obj.name} is bound as a column, which has no parts to index`, n.pos);
  }

  constantIndex(idx) {
    if (idx.t === 'num' || idx.t === 'text') return String(idx.v);
    refuse('E_SQL_SHAPE',
      'an index must be a constant here: the column it names has to be known '
      + 'before the query runs', idx.pos);
  }

  unary(n) {
    let x = this.node(n.x);
    if (n.op === 'NOT') {
      x = this.requireBool(x, n.x.pos, 'NOT');
    } else {
      this.requireNotBool(x, n.x.pos, n.op);
      this.requireNumericConstant(n.x);
    }
    return this.apply('ops', n.op, [x], n.pos);
  }

  binary(n) {
    const op = n.op;

    if (op === 'IN') return this.inOperator(n);

    let l = this.node(n.l);
    let r = this.node(n.r);

    if (op === 'AND' || op === 'OR' || op === 'XOR') {
      l = this.requireBool(l, n.l.pos, op);
      r = this.requireBool(r, n.r.pos, op);
    }
    // SEL reads both operands of an arithmetic or numeric-comparison operator as
    // numbers, and a BOOL is not one: `0 != FALSE` is E_NOT_NUM in the evaluator,
    // not FALSE. MariaDB and SQLite coerce a boolean to 1 or 0 and answer anyway —
    // a wrong answer with no error attached — and PostgreSQL says `cannot cast
    // type boolean to numeric` and fails the query.
    if (['+', '-', '*', '/', '%'].includes(op) || NUMERIC_OPS.includes(op)) {
      this.requireNotBool(l, n.l.pos, op);
      this.requireNotBool(r, n.r.pos, op);
      // And an operand whose value is written down has to BE a number. After the
      // BOOL guard, not before: `TRUE + 1` is E_SQL_SHAPE and stays that way.
      this.requireNumericConstant(n.l);
      this.requireNumericConstant(n.r);
    }
    // The `$` family and `&`, not EQL and IN: those two are structural and
    // `TRUE EQL TRUE` is TRUE, while `"x" $== TRUE` is E_NOT_BIN.
    if (op === '&' || (op[0] === '$' && op !== '$')) {
      this.requireNotBoolOperand(l, n.l.pos, op);
      this.requireNotBoolOperand(r, n.r.pos, op);
    }
    const variant = this.variantFor(op, [l, r]);
    if (BYTE_COMPARISONS.includes(op)) {
      requireComparableKinds(l, r, op, n.pos);
      // See Emit.textOperand for why the operands are transformed here rather
      // than by the template. Selected by operator, NOT by the variant being
      // named "text": `&` has a variant of that name too and is concatenation,
      // not a comparison — casting and collating its operands would be wrong and,
      // briefly, was.
      //
      // Two BIN operands are already bytes and are compared as bytes by every
      // dialect here, so the cast is not merely redundant: casting them to
      // characters made `TO_UTF8(X) EQL TO_UTF8(Y)` answer NULL on MariaDB and
      // MySQL whenever either side was not valid UTF-8, where SEL answers FALSE.
      // The corpus had exactly one BIN value, 7ac3a9, which is valid UTF-8 and
      // could not show it.
      if (l.kind !== 'BIN' || r.kind !== 'BIN') {
        l = this.emit.textOperand(l);
        r = this.emit.textOperand(r);
      }
    }
    return this.apply('ops', op, [l, r], n.pos, variant);
  }

  // `x IN list` is the one operator whose right operand is a list on purpose.
  // SEL's IN is EQL-based and therefore structural; SQL's is a value comparison
  // under a collation. For scalars under the binary collation the two agree, and
  // that is the only shape accepted.
  inOperator(n) {
    // The needle is rendered per branch, and per comparison in the list branch,
    // rather than once up front. Rendering it eagerly bound a value the list
    // branch then never used, leaving one more entry in `params` than there were
    // placeholders — the mirror of the bug below.
    const rhs = n.r;

    // `x IN rel` is the one place a relation appears on the right of an operator
    // rather than as an aggregate's source, so it is lowered here and not by
    // aggregate(). {body} is the relation's declared scalar.
    if (rhs.t === 'var' && this.binder(rhs.name) === null && this.bindings.has(rhs.name)) {
      const b = this.bindings.get(rhs.name, rhs.pos);
      if (b.kind === 'relation') {
        const scalar = (b.scalar ?? null) !== null ? asciiUpper(String(b.scalar)) : null;
        if (scalar === null || !Object.hasOwn(b.fields, scalar)) {
          refuse('E_SQL_SHAPE',
            `IN over ${rhs.name} needs the binding to name a "scalar" field: that is `
            + 'the column the subquery projects', rhs.pos);
        }
        // A relation with more than one field is a list of ROWS, and SEL compares
        // a scalar against a row structurally: it is FALSE for every row, always.
        // Projecting one column would translate something the evaluator never
        // answers — the oracle found this by asking both sides and getting [1]
        // from the server and [] from SEL. Bind the column as its own one-field
        // relation and the two agree; anything else is a guess about which field
        // the author meant.
        const nFields = Object.keys(b.fields).length;
        if (nFields !== 1) {
          refuse('E_SQL_SHAPE',
            `IN over ${rhs.name} is refused: the relation declares ${nFields} fields, `
            + 'so SEL reads its rows as maps and a scalar can never equal one. Bind '
            + 'the projected column as a relation with that one field.', rhs.pos);
        }
        return new Fragment(
          this.fillNamed(this.skeleton('inRelation', n.pos),
            slots(this.relationSlots(b), {
              needle: [this.emit.textOperand(this.node(n.l))],
              body: [this.emit.textOperand(this.columnRef(b.fields[scalar]))],
            }), n.pos),
          'BOOL', this.dialect);
      }
    }

    let elements = null;
    if (rhs.t === 'list') {
      elements = rhs.items;
    } else if (rhs.t === 'clist') {
      elements = rhs.entries.map(([, v]) => v);
    } else if (rhs.t === 'var' && this.binder(rhs.name) === null
               && this.bindings.has(rhs.name)) {
      // A `value` binding holding a list is the natural way a host writes an
      // allow-list, and §5.4 says it unrolls exactly like a literal list. The
      // elements are already synthesised into nodes for the aggregates; this
      // reuses that rather than adding a second path.
      const b = this.bindings.get(rhs.name, rhs.pos);
      if (b.kind === 'value' && b.value.size() > 0) {
        elements = [...this.valueElements(b, rhs.pos).values()].map((bd) => bd.payload);
      }
    }

    if (elements === null) {
      const r = this.node(rhs);              // a scalar; spec §5.4's second case
      const l = this.node(n.l);
      requireComparableKinds(l, r, 'IN', n.pos);
      return this.apply('ops', 'IN',
        [this.emit.textOperand(l), this.emit.textOperand(r)], n.pos, 'scalar');
    }

    // A literal list becomes a chain of byte comparisons rather than SQL's IN.
    // Casting each element inside a variadic template is not expressible, and
    // casting only the needle is not enough: on MariaDB 11.8,
    // CAST(3.0 AS CHAR) COLLATE utf8mb4_bin IN (3) is 1, because the numeric
    // right-hand side pulls the comparison back to numbers, where SEL says FALSE.
    // The cast has already cost the index SQL's IN would have used, so the chain
    // gives up nothing the fix had not already spent.
    if (elements.length === 0) return this.literal(Value.bool(false), 'BOOL');
    const tests = [];
    for (const e of elements) {
      // Needle first, because it is emitted first. A parameter slot is numbered
      // when it is created and a positional placeholder carries no number, so a
      // driver binds values in creation order to placeholders in text order — and
      // the two are the same order only if operands are rendered left to right.
      //
      // The needle is also rendered once per comparison rather than once and
      // spliced N times: splicing one Fragment twice puts the same slot number in
      // the output twice while `params` holds one entry.
      const raw = this.node(n.l);
      const needle = this.emit.textOperand(raw);
      const f = this.node(e);
      if (f.kind === 'LIST') {
        refuse('E_SQL_SHAPE',
          'IN over a list of lists is structural in SEL and has no SQL counterpart',
          e.pos);
      }
      requireComparableKinds(raw, f, 'IN', e.pos);
      tests.push(this.apply('ops', 'EQL',
        [needle, this.emit.textOperand(f)], e.pos, 'text'));
    }
    return this.foldPairwise('OR', tests, n.pos);
  }

  // Fold fragments pairwise-left through an operator's own template — the same
  // path a hand-written chain takes, so an unrolled aggregate and a written-out
  // chain produce the same bytes.
  foldPairwise(op, parts, pos) {
    let acc = parts[0];
    for (const nxt of parts.slice(1)) {
      acc = this.apply('ops', op, [acc, nxt], pos, this.variantFor(op, [acc, nxt]));
    }
    return acc;
  }

  call(n0) {
    let n = n0;
    const name = n.name;

    if (AGGREGATES.includes(name)) return this.aggregate(n);
    if (name === 'COUNT') return this.count(n);
    if (name === 'HAS') return this.has(n);
    if (name === 'INDEXES') {
      refuse('E_SQL_SHAPE',
        'INDEXES yields a list of keys, and a SQL expression is a scalar', n.pos);
    }
    if (name === 'ABORT') {
      refuse('E_SQL_UNSUPPORTED',
        'ABORT raises an error, which is a control-flow effect and not a value a '
        + 'SQL expression can be', n.pos);
    }
    if (name === 'IF' || name === 'COND') return this.conditional(n);

    n = this.rewriteRegex(n);

    const args = [];
    for (const arg of n.args) {
      const f = this.node(arg);
      if (f.kind === 'LIST') {
        refuse('E_SQL_SHAPE',
          `argument to ${name} is a list, and a SQL expression is a scalar`, arg.pos);
      }
      this.requireArgumentKind(name, f, arg.pos);
      args.push(f);
    }
    return this.apply('funcs', name, args, n.pos);
  }

  // Refuse an argument whose kind SEL would refuse.
  //
  // Nothing checked function arguments at all, and the operators' own guards did
  // not reach them. Measured on MariaDB: 26 functions accepted a BOOL and 26
  // accepted a BIN where SEL raises. `UPPER(BLOB)` answered '1' on three servers
  // and '\X31' on PostgreSQL; `LEN(BLOB)` answered 1 on three and 4 on
  // PostgreSQL. Every one of those is a translation reporting success for an
  // expression SEL has no answer for.
  //
  // A column is where this bites, which is why the constant check could not cover
  // it: `BLOB + 1` has no value to hand the evaluator, and only the declared kind
  // says anything.
  requireArgumentKind(name, f, pos) {
    if (f.kind === 'BOOL' && !BOOL_ARGUMENT_OK.includes(name)) {
      refuse('E_SQL_SHAPE',
        `${name} does not take a BOOL argument; SEL raises here rather than reading `
        + 'a boolean as text or as 1', pos);
    }
    if (f.kind === 'BIN' && !BIN_ARGUMENT_OK.includes(name)) {
      refuse('E_SQL_SHAPE',
        `${name} reads its argument as text, and this is BIN; SEL raises here rather `
        + 'than reinterpreting bytes as characters', pos);
    }
  }

  // Put a regex pattern through the language's own rewriter before it is emitted.
  //
  // Spec §7.8 expands \d, \w and \s into explicit ASCII classes rather than
  // passing them through, because otherwise a library flag decides what they mean
  // — and MariaDB's engine decides differently. Verified on 11.8: '٣' REGEXP
  // '^\d$' is 1 there and FALSE in SEL.
  //
  // The rewriter is the language's own. A copy here would be a second thing to
  // keep in step, and it would fail silently when they drifted.
  //
  // Both the pattern and the flags must be literals: a pattern read from a column
  // cannot be rewritten, and the flag selects the template.
  rewriteRegex(n) {
    if (!Object.hasOwn(REGEX_AT, n.name)) return n;
    const at = REGEX_AT[n.name];
    const pat = at < n.args.length ? n.args[at] : null;
    if (pat === null || pat.t !== 'text') {
      refuse('E_SQL_UNSUPPORTED',
        `${n.name} needs a literal pattern here: SEL rewrites \\d, \\w and \\s into `
        + 'explicit ASCII classes before matching, and a pattern that is not known '
        + 'until the query runs cannot be rewritten', pat !== null ? pat.pos : n.pos);
    }
    // validate raises SelError for a pattern outside the portable subset, and SEL
    // raises it too — but only when the call is reached. Translation walks every
    // branch, so a pattern in a branch the evaluator never takes reaches here
    // anyway, and a SelError escaping Sql.translate would break the one thing
    // tryTranslate() promises: that a rule which cannot be pushed down returns
    // null rather than raising. Found by the fuzz lane, as a fatal error in the
    // middle of a run.
    let source;
    try {
      // PHP names this portableSource, a one-line alias for validate(); this host
      // has only validate, whose comment says the same thing — "validates and
      // rewrites in one pass, returning source that means the same thing to every
      // engine". Calling it directly rather than adding an alias keeps the host
      // surface unchanged.
      source = regexValidate(String(pat.v), pat.pos);
    } catch (e) {
      if (!(e instanceof SelError)) throw e;
      refuse('E_SQL_UNSUPPORTED',
        `${n.name}'s pattern is not in SEL's portable subset, so there is nothing to `
        + `translate: ${e.message}`, pat.pos);
    }

    // Dotall is permanently on in SEL (spec §7.8) and off by default in the
    // server, so every pattern carries (?s). The modifier goes in the pattern
    // rather than in the template because the flag argument is not something the
    // template should see: selecting an arity-keyed template by argument count
    // gave every three-argument call the case-insensitive form, and left the flag
    // bound as a parameter nothing emitted.
    let inline = '(?s)';

    const flagAt = n.name === 'RREPLACE' ? 3 : 2;
    const args = [...n.args];
    if (flagAt >= args.length) {
      args[at] = litNode('text', inline + source, args[at].pos);
      return { ...n, args };
    }
    const flags = args[flagAt];
    if (flags.t !== 'text') {
      refuse('E_SQL_UNSUPPORTED',
        `${n.name} needs literal flags here: their content selects the mapping, so `
        + 'they have to be known before the query runs', flags.pos);
    }

    // The flag string's CONTENT chooses the template. Choosing by argument count
    // instead meant every three-argument call got the case-insensitive form, so
    // RMATCH(p, s, "") matched case-insensitively where SEL does not, and
    // RMATCH(p, s, "zzz") compiled happily where SEL raises E_BAD_ARG. An empty
    // flag string is dropped so the two-argument template applies.
    const text = String(flags.v);
    // Spelled as the two strings that pass rather than as a case fold: PHP's
    // strtolower is ASCII-only and JS's toLowerCase is not ("İ".toLowerCase() is
    // two code points), and `!== 'i'` admits exactly "i" and "I". Naming them is
    // byte-exact and needs no asciiLower.
    if (text !== '' && text !== 'i' && text !== 'I') {
      refuse('E_SQL_UNSUPPORTED',
        `${n.name} accepts only the i flag here, and SEL accepts only i at all; `
        + `${quoteDump(text)} is not it`, flags.pos);
    }
    if (text !== '') {
      // The evaluator refuses i on a pattern with non-ASCII literals, because case
      // folding above ASCII is the one thing PCRE and ECMAScript cannot be made to
      // agree on. A translation that accepted it would disagree with the host that
      // refused it.
      for (const cp of toCodePoints(source)) {
        if (cp > 0x7f) {
          refuse('E_SQL_UNSUPPORTED',
            'the i flag needs an ASCII-only pattern, which SEL requires for the same '
            + 'reason and refuses here too', flags.pos);
        }
      }
      inline = '(?si)';
    }
    args[at] = litNode('text', inline + source, args[at].pos);
    args.splice(flagAt, 1);                  // folded into the pattern
    return { ...n, args };
  }

  // IF and COND are the same construct: condition/result pairs and a default.
  // IF's two-argument form defaults to TEXT "" exactly as spec §7.2 says, so one
  // builder covers both and the CASE skeleton has one shape.
  conditional(n) {
    const args = [...n.args];
    if (n.name === 'IF' && args.length === 2) args.push(litNode('text', '', n.pos));

    const branchTpl = this.skeleton('caseBranch', n.pos);
    const caseTpl = this.skeleton('case', n.pos);

    const branches = [];
    const results = [];
    const last = args.length - 1;
    for (let i = 0; i < last; i += 2) {
      const cond = this.requireBool(this.node(args[i]), args[i].pos, n.name);
      const then = this.node(args[i + 1]);
      results.push(then);
      branches.push(new Fragment(
        this.fillNamed(branchTpl, { cond: [cond], then: [then] }, n.pos),
        'UNKNOWN', this.dialect));
    }
    const els = this.node(args[last]);
    results.push(els);

    // The branches are joined by the skeleton's own spacing, not by ", ".
    const joined = [];
    branches.forEach((b, i) => {
      if (i > 0) joined.push(' ');
      joined.push(b);
    });
    const parts = this.fillNamed(caseTpl, { branches: joined, else: [els] }, n.pos);
    return new Fragment(parts, unify(results, n.pos), this.dialect);
  }

  // --- aggregates: docs/SQL-TRANSLATION.md §7 ------------------------------
  //
  // Lowering runs inside this walk rather than as an AST pass before it. Two of
  // the three shapes have to render — a relation becomes a subquery, which is
  // characters — and the third needs the dialect's operator templates, which a
  // tree rewrite has no access to.

  binder(name) {
    for (let i = this.frames.length - 1; i >= 0; i -= 1) {
      if (this.frames[i].has(name)) return this.frames[i].get(name);
    }
    return null;
  }

  fromBinder(b, n) {
    if (b.shape === Binder.NODE) return this.node(b.payload);
    if (b.shape === Binder.COLUMN) return this.columnRef(b.payload);
    if (b.shape === Binder.ROW) {
      const rel = b.payload;
      // The guard `IN` got and nothing else did. A row of a relation with more
      // than one field is a MAP in SEL, and a map is not the value of one of its
      // fields: `ANY(ITEMS, _ $== "AB-1000")` is [] in SEL, because comparing a
      // map against text is structurally false for every row, and was [1] on all
      // four servers. That is byte for byte the multi-field IN defect, reached
      // through the bare binder instead. A one-field relation is genuinely a
      // scalar and keeps working.
      const nFields = Object.keys(rel.fields).length;
      if (nFields > 1) {
        refuse('E_SQL_SHAPE',
          `${n.name} is a row of a relation with ${nFields} fields, which is a map `
          + 'in SEL and not one value; name the field you mean', n.pos);
      }
      const scalar = (rel.scalar ?? null) !== null ? asciiUpper(String(rel.scalar)) : null;
      if (scalar === null || !Object.hasOwn(rel.fields, scalar)) {
        refuse('E_SQL_SHAPE',
          `${n.name} names a row, and the relation does not say which of its fields `
          + 'a bare reference means; give the binding a "scalar", or index the field '
          + 'you want', n.pos);
      }
      return this.columnRef(rel.fields[scalar]);
    }
    refuse('E_SQL_SHAPE', String(b.reason), n.pos);
  }

  indexBinder(b, name, key, n) {
    if (b.shape === Binder.ROW) {
      if (listKey(key) !== null) {
        refuse('E_SQL_SHAPE',
          `${name}[${key}] asks for a row by position, and a relation has no first `
          + 'row without an ORDER BY that nothing here can supply', n.pos);
      }
      const field = asciiUpper(key);
      if (!Object.hasOwn(b.payload.fields, field)) {
        const known = Object.keys(b.payload.fields).sort();
        const tail = known.length === 0 ? '; it declares none' : `; it has ${known.join(', ')}`;
        refuse('E_SQL_BINDING',
          `${name}["${key}"] is not a field of that relation${tail}`, n.pos);
      }
      return this.columnRef(b.payload.fields[field]);
    }
    if (b.shape === Binder.NODE) {
      const elem = childOf(b.payload, key);
      if (elem === null) {
        refuse('E_SQL_BINDING', `${name}["${key}"] is not a key of that element`, n.pos);
      }
      return this.node(elem);
    }
    refuse('E_SQL_SHAPE',
      `${name} names a single column, which has no parts to index`, n.pos);
  }

  // Classify an aggregate's first argument into one of the three shapes,
  // absorbing any FILTER on the way through. Recursive, so
  // `FILTER(FILTER(L, p1), p2)` conjoins both predicates over L.
  source(src, call) {
    if (src.t === 'call' && src.name === 'FILTER') {
      const [fBinder, fBody] = aggShape(src);
      const inner = this.source(src.args[0], call);
      inner.filters.push({ binder: fBinder, body: fBody });
      return inner;
    }
    if (src.t === 'call' && src.name === 'MAP') {
      refuse('E_SQL_UNSUPPORTED',
        'MAP as the thing an aggregate iterates is not translated: unlike FILTER, '
        + 'which only decides whether an element takes part, MAP changes what the '
        + 'element is, so the two binders mean different things and binding both to '
        + 'one element is not enough. See docs/SQL-TRANSLATION.md §7.5', src.pos);
    }

    // Built by a helper rather than by a merge: PHP's array union keeps the LEFT
    // operand for a duplicated key, so `$base + [… 'scalarRule' => true]` left
    // scalarRule permanently false and made COUNT of a scalar answer 1 where the
    // evaluator answers 0.
    if (src.t === 'list') {
      const m = new Map();
      src.items.forEach((item, i) => m.set(String(i + 1), Binder.node(item)));
      return staticSource(m);
    }
    if (src.t === 'clist') {
      const m = new Map();
      for (const [k, v] of src.entries) m.set(k, Binder.node(v));
      return staticSource(m);
    }

    if (src.t === 'var') {
      const bound = this.binder(src.name);
      if (bound !== null) {
        if (bound.shape === Binder.NODE) return this.source(bound.payload, call);
        if (bound.shape === Binder.NONE) refuse('E_SQL_SHAPE', String(bound.reason), src.pos);
        // A column is one value, so it is a one-element list containing itself —
        // spec §7.3, the same rule the evaluator applies. This is what makes
        // ALL(V, ALL(V, …)) work.
        //
        // A multi-field ROW is not one value, and applying the scalar rule to it
        // answered for a different question: COUNT(I) folded to 0 where SEL says
        // 3, HAS(I, "QTY") to FALSE where SEL says TRUE, and ANY(I, …) iterated
        // nothing where SEL iterates the row's values.
        if (bound.shape === Binder.ROW && Object.keys(bound.payload.fields).length > 1) {
          refuse('E_SQL_SHAPE',
            `${src.name} is a row of a multi-field relation, which is a map with one `
            + 'child per field; SQL has no way to iterate or count that', src.pos);
        }
        return staticSource(new Map([['1', bound]]), true);
      }
      const b = this.bindings.get(src.name, src.pos);
      if (b.kind === 'relation') {
        return { shape: 'relation', relation: b, filters: [], scalarRule: false };
      }
      if (b.kind === 'columns') {
        const m = new Map();
        b.items.forEach((item, i) => m.set(String(i + 1), Binder.column(item)));
        return { shape: 'columns', elements: m, filters: [], scalarRule: false };
      }
      if (b.kind === 'value') {
        const v = b.value;
        // A scalar value binding is one value, so the scalar rule applies to it
        // exactly as it does to a column.
        return staticSource(this.valueElements(b, src.pos), v.size() === 0 && !v.isNone());
      }
    }

    // Anything else that is one value: the scalar rule again — but only if it IS
    // one value. A call that yields a list is not, and treating one as a scalar is
    // how three map refusals were bypassed: COUNT(SPLIT("a,b", ",")) folded to 0
    // where SEL says 2, and HAS(SPLIT(…), 1) to FALSE where SEL says TRUE. The
    // same functions refuse correctly under ALL, SUM, JOIN and FILTER, which is
    // what made it hard to see. The refusal string in the dialect document says
    // "yields a list, and a SQL expression is a scalar"; this is the path that
    // never asked it.
    if (src.t === 'call' && YIELDS_LIST.includes(src.name)) {
      refuse('E_SQL_SHAPE',
        `${src.name} yields a list, and the scalar rule does not apply to it; SQL has `
        + 'no way to count or index what it produces', src.pos);
    }
    return staticSource(new Map([['1', Binder.node(src)]]), true);
  }

  // A `value` binding holds Values, not AST nodes, so its children are
  // synthesised into nodes before binding. A child with children becomes a
  // `clist`; a scalar becomes the node kind its declared type asks for, which is
  // the same rule declaredKind applies to the value as a whole — so what decides
  // quoting is stated once.
  valueElements(b, pos) {
    const v = b.value;
    const out = new Map();
    if (v.size() === 0) {
      // A NONE with no children is genuinely empty — what FILTER returns when
      // nothing matched. A scalar is a one-element list of itself.
      if (v.isNone()) return out;
      out.set('1', Binder.node(this.valueNode(v, b, pos)));
      return out;
    }
    for (const [k, child] of v.entries()) {
      out.set(k, Binder.node(this.valueNode(child, b, pos)));
    }
    return out;
  }

  valueNode(v, b, pos) {
    if (v.size() > 0) {
      return new normalise.CList(pos,
        v.entries().map(([k, child]) => [k, this.valueNode(child, b, pos)]));
    }
    if (v.isBool()) return litNode('bool', v.asBool(pos), pos);
    if (v.isBin()) {
      refuse('E_SQL_SHAPE',
        'a BIN element of a value binding has no literal node to become; bind it as '
        + 'a column, or convert it before translating', pos);
    }
    return litNode(b.type === 'NUM' ? 'num' : 'text', v.asText(pos), pos);
  }

  aggregate(n) {
    const name = n.name;
    if (name === 'MAP' || name === 'FILTER') {
      refuse('E_SQL_SHAPE',
        `${name} yields a list, and a SQL expression is a scalar; it can only be the `
        + 'thing another aggregate iterates', n.pos);
    }
    if (name === 'JOIN') return this.joinAggregate(n);

    const [binderName, body] = aggShape(n);
    const src = this.source(n.args[0], n);

    if (src.shape === 'relation') {
      const rendered = this.withRow(src, binderName, () => this.aggBody(name, body, src, n));
      return this.relationAggregate(name, src.relation, rendered, n);
    }

    const parts = [];
    for (const [key, elem] of src.elements) {
      parts.push(this.withElement(src, binderName, elem, String(key), n,
        () => this.aggBody(name, body, src, n)));
    }
    if (parts.length === 0) {
      if (name === 'ALL') return this.literal(Value.bool(true), 'BOOL');   // spec §7.3
      if (name === 'ANY') return this.literal(Value.bool(false), 'BOOL');
      return this.literal(Value.num('0'), 'NUM');
    }
    if (parts.length === 1) return parts[0];
    return this.foldPairwise(AGG_FOLD[name], parts, n.pos);
  }

  // Render the body, and combine it with any absorbed FILTER predicates.
  //
  // The four rewrites of §7.5, and each is NULL-safe under the skeletons of §7.3:
  // for ALL a NULL predicate with a FALSE body gives a NULL result, which
  // `IS NOT TRUE` includes — the element is treated as having been in the filter
  // and having failed, which is the conservative reading.
  aggBody(name, body, src, n) {
    let q = this.node(body);
    q = name === 'SUM' ? this.requireNum(q, body.pos, name)
      : this.requireBool(q, body.pos, name);

    for (const f of src.filters) {
      const p = this.requireBool(this.node(f.body), f.body.pos, 'FILTER');
      if (name === 'SUM') {
        q = this.caseWhen(p, q, this.literal(Value.num('0'), 'NUM'), n.pos);
        continue;
      }
      if (name === 'ALL') {
        q = this.apply('ops', 'OR', [this.apply('ops', 'NOT', [p], n.pos), q], n.pos);
      } else {
        q = this.apply('ops', 'AND', [p, q], n.pos);
      }
    }
    return q;
  }

  // Push a frame for one element of a static or columns unroll and render.
  //
  // Every absorbed FILTER's binder is bound to the same element, which is what
  // makes absorption three lines rather than a substitution pass — see §7.5.
  withElement(src, binderName, elem, key, n, render) {
    const frame = new Map([
      [binderName, elem],
      ['_K', Binder.node(litNode('text', key, n.pos))],
    ]);
    for (const f of src.filters) frame.set(f.binder, elem);
    this.frames.push(frame);
    try {
      return render();
    } finally {
      this.frames.pop();
    }
  }

  // The same for a relation, where there is one frame rather than one per
  // element.
  //
  // `_K` is in scope only to refuse: a row has no portable key, and inventing one
  // — ROW_NUMBER(), the primary key — would be a guess about the schema this layer
  // is careful never to make.
  withRow(src, binderName, render) {
    // A relation nested inside itself reuses its own fixed alias, and the inner
    // FROM shadows the outer one, so the predicate is constantly false and all
    // four servers answered [] where SEL answers [1,4,5]. Bindings.checkAliases
    // dedupes across *distinct* binding names, and this is one name, so it could
    // never fire. Re-aliasing is not available as a fix: `correlate` is
    // host-written SQL that spells the alias itself. Two different relations with
    // distinct aliases nest correctly.
    const alias = relationAlias(src.relation);
    for (const frame of this.frames) {
      for (const bd of frame.values()) {
        if (bd.shape === Binder.ROW && relationAlias(bd.payload) === alias) {
          refuse('E_SQL_SHAPE',
            `this relation is already open as ${alias} further out, and a subquery `
            + 'reusing its own alias shadows the outer row rather than comparing '
            + 'against it; the correlation names the alias, so it cannot be renamed '
            + 'here', src.pos);
        }
      }
    }

    const row = Binder.row(src.relation);
    const frame = new Map([
      [binderName, row],
      ['_K', Binder.none('a row of a relation has no key: SQL rows are unordered '
        + 'and unkeyed unless the schema says otherwise, and guessing which column '
        + 'is the key is not something this layer does')],
    ]);
    for (const f of src.filters) frame.set(f.binder, row);
    this.frames.push(frame);
    try {
      return render();
    } finally {
      this.frames.pop();
    }
  }

  relationAggregate(name, rel, body, n) {
    return new Fragment(
      this.fillNamed(this.skeleton(AGG_SKELETON[name], n.pos),
        slots(this.relationSlots(rel), { body: [body] }), n.pos),
      AGG_RETURNS[name], this.dialect);
  }

  // `{from}` is the table and alias, or a query the binding carries; `{corr}` is
  // the join back to the outer row, or the dialect's TRUE when the binding has
  // none — an uncorrelated relation is a subquery over the whole table, which is
  // legal and occasionally what you want.
  relationSlots(rel) {
    const frm = rel.from;
    let out = (frm !== null && typeof frm === 'object' && Object.hasOwn(frm, 'raw'))
      ? String(frm.raw) : this.emit.ident(String(frm));
    if (rel.alias) out += ` ${this.emit.ident(String(rel.alias))}`;
    const corr = rel.correlate ?? null;
    return { from: [out], corr: [corr ? String(corr.raw) : String(this.emit.lex('true'))] };
  }

  count(n) {
    const src = this.source(n.args[0], n);

    // COUNT is the number of children, so the scalar rule does not apply to it:
    // spec §7.4 says a value with no children counts 0, where §7.3's one-element
    // rule is about what an aggregate iterates.
    if (src.shape !== 'relation' && src.scalarRule && src.filters.length === 0) {
      return this.literal(Value.num('0'), 'NUM');
    }
    if (src.filters.length > 0) {
      // COUNT(FILTER(L, p)) is SUM(L, CASE WHEN p THEN 1 ELSE 0 END).
      const body = litNode('num', '1', n.pos);
      if (src.shape === 'relation') {
        const rendered = this.withRow(src, '_', () => this.aggBody('SUM', body, src, n));
        return this.relationAggregate('SUM', src.relation, rendered, n);
      }
      const parts = [];
      for (const [key, elem] of src.elements) {
        parts.push(this.withElement(src, '_', elem, String(key), n,
          () => this.aggBody('SUM', body, src, n)));
      }
      if (parts.length === 0) return this.literal(Value.num('0'), 'NUM');
      return parts.length === 1 ? parts[0] : this.foldPairwise('+', parts, n.pos);
    }
    if (src.shape === 'relation') {
      return new Fragment(
        this.fillNamed(this.skeleton('count', n.pos), this.relationSlots(src.relation),
          n.pos),
        'NUM', this.dialect);
    }
    return this.literal(Value.num(String(src.elements.size)), 'NUM');
  }

  has(n) {
    if (n.args[1].t !== 'text' && n.args[1].t !== 'num') {
      refuse('E_SQL_SHAPE',
        'HAS needs a constant key here: which column it asks about has to be known '
        + 'before the query runs', n.args[1].pos);
    }
    const key = String(n.args[1].v);
    const src = this.source(n.args[0], n);
    if (src.filters.length > 0) {
      refuse('E_SQL_SHAPE',
        'HAS over a FILTER would have to know at translation time which elements the '
        + 'filter kept', n.pos);
    }
    // A relation is a list of row maps, so its keys are "1", "2", … and never a
    // field name — the row oracle's own loader builds it that way. Answering from
    // the declared fields asked a different question and got both directions
    // wrong: HAS(ITEMS, "QTY") was TRUE where SEL says FALSE, and HAS(SKUS, "1")
    // was FALSE where SEL says TRUE. The positional direction cannot be answered
    // here at all — it needs the row count — so refusal is the only honest outcome
    // for either.
    if (src.shape === 'relation') {
      refuse('E_SQL_SHAPE',
        'HAS over a relation asks whether it has a key, and a relation is a list of '
        + 'rows whose keys are positions; the answer needs the row count, which no '
        + 'expression here knows', n.pos);
    }
    const found = !src.scalarRule && src.elements.has(key);
    return this.literal(Value.bool(found), 'BOOL');
  }

  // JOIN is strict, not an aggregate: its second argument is a separator.
  //
  // Folded pairwise through the dialect's own concatenation, because `&` is what
  // SEL's JOIN is, and a variadic concat would need a lexical key spelled two
  // ways for the sake of one function.
  joinAggregate(n) {
    const src = this.source(n.args[0], n);
    if (src.shape === 'relation') {
      const rel = src.relation;
      const scalar = (rel.scalar ?? null) !== null ? asciiUpper(String(rel.scalar)) : null;
      if (scalar === null || !Object.hasOwn(rel.fields, scalar)) {
        refuse('E_SQL_SHAPE',
          'JOIN over a relation needs the binding to name a "scalar" field', n.pos);
      }
      const body = this.columnRef(rel.fields[scalar]);
      const skel = this.skeleton('join', n.pos);      // refuses with the map's reason
      return new Fragment(
        this.fillNamed(skel, slots(this.relationSlots(rel),
          { body: [body], sep: [this.node(n.args[1])] }), n.pos),
        'TEXT', this.dialect);
    }

    const parts = [];
    for (const [key, elem] of src.elements) {
      if (parts.length > 0) {
        // Rendered per gap, not once and reused: see the note in inOperator on
        // why splicing one Fragment twice breaks `params`.
        parts.push(this.node(n.args[1]));
      }
      parts.push(this.withElement(src, '_', elem, String(key), n,
        () => this.fromBinder(elem, n)));
    }
    if (parts.length === 0) return this.literal(Value.text(''), 'TEXT');
    return parts.length === 1 ? parts[0] : this.foldPairwise('&', parts, n.pos);
  }

  caseWhen(cond, then, els, pos) {
    const branch = new Fragment(
      this.fillNamed(this.skeleton('caseBranch', pos), { cond: [cond], then: [then] }, pos),
      'UNKNOWN', this.dialect);
    return new Fragment(
      this.fillNamed(this.skeleton('case', pos), { branches: [branch], else: [els] }, pos),
      then.kind === els.kind ? then.kind : 'UNKNOWN', this.dialect);
  }

  // --- map application -----------------------------------------------------

  // Look one entry up, check it, and fill it.
  //
  // Every refusal in the map arrives here, and a refusal spelled as a string in
  // the map becomes the message the caller reads — which is what makes one error
  // class enough.
  apply(section, key, args, pos, variant = null) {
    const entry = map.entry(this.dialect, section, key);
    const what = section === 'ops' ? `the ${key} operator` : key;

    if (entry === map.MISSING || entry === null || entry === undefined) {
      refuse('E_SQL_UNSUPPORTED',
        `${what} has no mapping in dialect ${this.dialect}`, pos);
    }
    if (typeof entry === 'string') {
      refuse('E_SQL_UNSUPPORTED',
        `${what} has no mapping in dialect ${this.dialect} — ${entry}`, pos);
    }
    // `?? null` against null, not a membership test: these transcribe isset(),
    // which is false for an explicit null. Reachable through a runtime define()
    // carrying a null field, which is the documented escape hatch.
    if ((entry.builder ?? null) !== null) {
      return entry.builder(this.emit, args, { pos });
    }

    // The map's `arity` narrows SEL's own for this dialect, and sql/MAP.md §4.1
    // gives it a job: "how PostgreSQL refuses the three-argument form its POSITION
    // cannot express while MariaDB still accepts it — graceful degradation as
    // data, with no host code involved." There was no host code involved, and
    // there was no degradation either: the field was validated by the generator
    // and enforced by nobody, so a narrowed entry took the call anyway and filled
    // the template it had, silently dropping the arguments the template did not
    // name. Found the hour sqlite was written, by the oracle: FIND("a","banana",3)
    // is 4 in SEL and instr('banana','a') is 2.
    if ((entry.arity ?? null) !== null) {
      const [lo, hi] = entry.arity;
      if (!(lo <= args.length && args.length <= hi)) {
        refuse('E_SQL_UNSUPPORTED',
          `${what} takes ${lo} to ${hi} argument(s) in dialect ${this.dialect}, and `
          + `this call has ${args.length}`, pos);
      }
    }

    if ((entry.since ?? null) !== null
        && !map.versionAtLeast(map.version(this.dialect), entry.since)) {
      refuse('E_SQL_DIALECT',
        `${what} needs ${this.dialect} ${entry.since}, and this map assumes `
        + `${map.version(this.dialect)}`, pos);
    }
    if ((entry.caveat ?? null) !== null) {
      if (this.strict) {
        refuse('E_SQL_UNSUPPORTED',
          `${what} maps to something that is not exactly equivalent (${entry.caveat}), `
          + 'and strict mode refuses those', pos);
      }
      this.caveats.add(entry.caveat);
    }

    const tpl = this.templateOf(entry, args, variant, what, pos);
    return new Fragment(this.emit.fill(tpl, args, pos), retKind(entry, args, pos),
      this.dialect);
  }

  templateOf(entry, args, variant, what, pos) {
    if ((entry.variants ?? null) !== null) {
      if (variant === null || !Object.hasOwn(entry.variants, variant)
          || entry.variants[variant] === null) {
        const shape = variant === null ? 'this shape' : `${variant} operands`;
        refuse('E_SQL_UNSUPPORTED',
          `${what} has no mapping in dialect ${this.dialect} for ${shape}`, pos);
      }
      return String(entry.variants[variant]);
    }
    const tpl = entry.tpl;
    if (typeof tpl === 'string') return tpl;
    let n = String(args.length);
    // `*` is the fallback for counts the entry does not NAME, and naming a count
    // with null names it: sql/MAP.md §2 says null is a refusal without a reason,
    // so `{"1": null, "*": …}` withdraws the one-argument form and the fallback
    // must not rescue it. Membership rather than a null test is the whole of that
    // rule; without it the withdrawal was unwritable, and the null reached the
    // renderer and emitted the literal text `null` into SQL.
    if (!Object.hasOwn(tpl, n) && Object.hasOwn(tpl, '*')) n = '*';
    if (!Object.hasOwn(tpl, n) || tpl[n] === null) {
      refuse('E_SQL_UNSUPPORTED',
        `${what} has no mapping in dialect ${this.dialect} for ${n} argument(s); it `
        + `maps ${Object.keys(tpl).sort().join(', ')}`, pos);
    }
    return String(tpl[n]);
  }

  // Which variant a family selects.
  //
  // Not in the data — sql/MAP.md §4.3 fixes three selectors and every host
  // implements them identically.
  variantFor(op, args) {
    if (NUMERIC_OPS.includes(op)) {
      return args[0].kind === 'NUM' && args[1].kind === 'NUM' ? 'num' : 'coerce';
    }
    if (TEXTUAL_OPS.includes(op)) return 'text';
    if (op === '&') return args[0].kind === 'BIN' || args[1].kind === 'BIN' ? 'bin' : 'text';
    return null;
  }

  // --- kind guards ---------------------------------------------------------

  // A number was expected and a boolean cannot become one.
  //
  // UNKNOWN passes, as everywhere: the binding did not say, so nothing here can
  // either. Only a *declared* BOOL is refused, which is the same line the
  // byte-comparison guard draws.
  requireNotBool(f, pos, where) {
    // spec §4: "BOOL and BIN are never numbers". The guard implemented the first
    // half of that sentence for a milestone: `BLOB + 1` translated, and MariaDB
    // answered 2.0 while MySQL answered 50 — two servers, two answers, neither
    // SEL's, from a column the schema said was binary.
    if (f.kind !== 'BOOL' && f.kind !== 'BIN') return;
    const what = f.kind === 'BOOL' ? 'a BOOL' : 'a BIN';
    refuse('E_SQL_SHAPE',
      `${where} reads its operands as numbers, and ${what} is not one; SEL answers `
      + 'E_NOT_NUM here rather than coercing it', pos);
  }

  // `&` takes text or bytes, and a BOOL is neither — spec §5.2 ends "BOOL is
  // E_NOT_TEXT". It was left out of the arithmetic list because it is not
  // arithmetic, and nothing else covered it: `FLAG & NAME` concatenated, and
  // MariaDB answered '1a' where PostgreSQL answered 'truea'.
  requireNotBoolOperand(f, pos, where) {
    if (f.kind !== 'BOOL') return;
    refuse('E_SQL_SHAPE',
      `${where} reads its operands as text or bytes, and a BOOL is neither; SEL `
      + 'answers E_NOT_TEXT here rather than spelling it 1 or true', pos);
  }

  // An operand in a numeric position whose value is knowable here.
  //
  // The guards above ask what the binding *declared*; this asks what the constant
  // *is*, which is a different and stronger question wherever the answer is
  // written down. See constants.requireNumeric for why refusing loses nothing,
  // and for why it is never keyed on a declared kind.
  requireNumericConstant(n) {
    if (constants.isConstant(n, this.constNames)) {
      constants.requireNumeric(n, this.constCtx);
    }
  }

  requireBool(f, pos, where) {
    if (f.kind === 'BOOL' || f.kind === 'UNKNOWN') return f;
    refuse('E_SQL_SHAPE',
      `${where} needs a BOOL here and this is ${f.kind}; SEL has no truthiness, so `
      + 'neither does its translation', pos);
  }

  // SUM's counterpart to requireBool. UNKNOWN passes for the same reason it does
  // there: an undeclared column may well be numeric, and the database is the one
  // that knows.
  requireNum(f, pos, where) {
    if (f.kind === 'NUM' || f.kind === 'UNKNOWN') return f;
    refuse('E_SQL_SHAPE',
      `${where} adds its body up, so it needs a number here and this is ${f.kind}`, pos);
  }

  // --- skeletons -----------------------------------------------------------

  skeleton(name, pos) {
    const s = map.entry(this.dialect, 'skel', name);
    if (s === map.MISSING || s === null || s === undefined) {
      refuse('E_SQL_UNSUPPORTED', `dialect ${this.dialect} has no ${name} skeleton`, pos);
    }
    if (typeof s === 'string') {
      refuse('E_SQL_UNSUPPORTED',
        `dialect ${this.dialect} cannot express ${name} — ${s}`, pos);
    }
    // A skeleton may carry a caveat, and until MariaDB's CASE needed one nothing
    // here read it — so `skel` was the one section whose entries could declare an
    // inexactness that never reached Fragment.caveats and that `strict` never
    // refused.
    if ((s.caveat ?? null) !== null) {
      if (this.strict) {
        refuse('E_SQL_UNSUPPORTED',
          `the ${name} skeleton for ${this.dialect} is not exactly equivalent `
          + `(${s.caveat}), and strict mode refuses those`, pos);
      }
      this.caveats.add(s.caveat);
    }
    return String(s.tpl);
  }

  // Fill a skeleton, whose placeholders are named rather than numbered.
  fillNamed(tpl, slotMap, pos) {
    const parts = [];

    const push = (s) => {
      if (s === '') return;
      if (parts.length && typeof parts[parts.length - 1] === 'string') {
        parts[parts.length - 1] += s;
      } else {
        parts.push(s);
      }
    };

    let i = 0;
    const nTpl = tpl.length;
    while (i < nTpl) {
      if (tpl[i] !== '{') { push(tpl[i]); i += 1; continue; }
      const end = tpl.indexOf('}', i);
      if (end === -1) { push(tpl.slice(i)); break; }
      const name = tpl.slice(i + 1, end);
      i = end + 1;
      if (!Object.hasOwn(slotMap, name)) {
        refuse('E_SQL_UNSUPPORTED',
          `a skeleton in dialect ${this.dialect} uses {${name}}, which is not one of `
          + 'its slots', pos);
      }
      for (const item of slotMap[name]) {
        if (typeof item === 'string') { push(item); continue; }
        for (const p of item.parts) {
          if (typeof p === 'string') push(p);
          else parts.push(p);
        }
      }
    }
    return parts;
  }
}

// --- module-level helpers ----------------------------------------------------

function staticSource(elements, scalarRule = false) {
  return { shape: 'static', elements, filters: [], scalarRule };
}

// The 2- and 3-argument forms: `_` by default, a bare name when given.
function aggShape(n) {
  if (n.args.length === 3) {
    if (!constants.isBinderName(n.args[1])) {
      refuse('E_SQL_SHAPE', `the binder of ${n.name} must be a bare name`, n.args[1].pos);
    }
    return [n.args[1].name, n.args[2]];
  }
  return ['_', n.args[1]];
}

// The alias a relation binding renders under — its own, or the table name when it
// declares none. The same rule Bindings.checkAliases applies.
function relationAlias(rel) {
  const alias = rel.alias ?? null;
  if (typeof alias === 'string' && alias !== '') return alias;
  const frm = rel.from ?? null;
  if (frm !== null && typeof frm === 'object') return String(frm.raw ?? '');
  return String(frm ?? '');
}

function childOf(node, key) {
  if (node.t === 'list') {
    const i = listKey(key);
    if (i === null || i > node.items.length) return null;
    return node.items[i - 1];
  }
  if (node.t === 'clist') {
    for (const [k, v] of node.entries) if (k === key) return v;
  }
  return null;
}

// Refuse a structural comparison between two different known kind classes.
//
// These operators compare kinds first, and no amount of casting says that in SQL.
// `CAST(0 AS CHAR)` and `CAST(FALSE AS CHAR)` are both '0', so `NOT (0 IN FALSE)`
// answered TRUE in SEL and FALSE on the server — found by the fuzz lane, which
// produces operand pairs a hand-written corpus does not.
//
// It used to compare only BOOL-ness, which let BIN through: SEL says
// `TO_UTF8("a") EQL "a"` is FALSE, and the emitted comparison cast both sides to
// characters and answered 1 on MariaDB, MySQL and SQLite. Refused rather than
// folded to FALSE — folding is the road §11.4 closed.
//
// The same call serves the `$` family, for a different reason with the same
// answer. SEL's byte comparisons do NOT compare kinds — `TO_UTF8("a") $== "a"` is
// genuinely TRUE, both operands read as bytes — but saying that in SQL means
// casting the TEXT side to bytes, and the templates cast the BIN side to
// characters instead, which is backwards: PostgreSQL compares against the literal
// `\x61` and answers false. Per-dialect byte casts for a comparison nobody writes
// is not a trade worth making, so mixed operands are refused here too. Two
// operands of one class still translate, and for two BINs the character cast is
// skipped — see binary().
//
// One UNKNOWN is the accepted limit — the binding did not say, so nothing here
// can either.
function requireComparableKinds(l, r, op, pos) {
  const cl = Object.hasOwn(EQL_CLASS, l.kind) ? EQL_CLASS[l.kind] : null;
  const cr = Object.hasOwn(EQL_CLASS, r.kind) ? EQL_CLASS[r.kind] : null;
  if (cl === null || cr === null || cl === cr) return;
  const other = l.kind === 'BOOL' ? r.kind : l.kind;
  refuse('E_SQL_SHAPE',
    `${op} compares a BOOL with a ${other}, which SEL answers FALSE for every value `
    + 'because the kinds differ. SQL has no way to say that: both sides cast to the '
    + 'same characters', pos);
}

function retKind(entry, args, pos = null) {
  const ret = String(entry.ret);
  if (ret === '@concat') return args.some((a) => a.kind === 'BIN') ? 'BIN' : 'TEXT';
  if (ret.startsWith('@unify:')) {
    const pick = ret.slice(7).split(',')
      .map(Number).filter((i) => i < args.length).map((i) => args[i]);
    return unify(pick, pos);
  }
  return ret;
}

// The one kind a set of branches all produce.
//
// UNKNOWN unifies with anything: that is what it is for, and a column whose type
// the binding did not declare is the ordinary case. Two *known* kinds that differ
// are another matter, and used to yield UNKNOWN as well. They cannot: SQL types
// the whole CASE, and there is no rendering of the result that agrees with SEL's.
//
// `IF(TRUE, TRUE, "A-1")` is the one the fuzz lane found. SEL answers the BOOL
// TRUE, whose text is "TRUE"; the CASE answers 1. Nothing casts one to the other
// and no caveat says "a boolean becomes 1", so the honest outcome is a refusal.
// `IF(p, 1, "x")` goes the same way for the same reason.
//
// The rule is one line: known-kind branches must agree.
function unify(fs, pos = null) {
  let kind = null;
  for (const f of fs) {
    if (f.kind === 'UNKNOWN') continue;
    if (kind === null) kind = f.kind;
    else if (kind !== f.kind) {
      refuse('E_SQL_SHAPE',
        `these branches produce different kinds — ${kind} and ${f.kind} — and SQL `
        + 'gives the whole expression one type, which cannot match SEL\'s for both',
        pos);
    }
  }
  return kind ?? 'UNKNOWN';
}

// The kind of a value supplied by the host, which is the one place the num/text
// ambiguity cannot be resolved from the AST — there is no AST, the host handed us
// a Value.
//
// So it is not guessed. BOOL and BIN are unambiguous; everything else is TEXT, and
// therefore quoted, unless the binding declares `type: NUM`. Guessing from
// looksNumeric() would emit a product code of "00123" as the number 123, and a
// rule comparing it with $== would then be answered by the database rather than by
// SEL's semantics.
function declaredKind(b, v) {
  if (v.isBool()) return 'BOOL';
  if (v.isBin()) return 'BIN';
  if (v.isNone()) return 'LIST';
  return b.type === 'NUM' ? 'NUM' : 'TEXT';
}

// Merge named slot maps for a skeleton, refusing to let one shadow another.
//
// The three relation skeletons are filled from two sources: relationSlots()
// supplies `from` and `corr`, the caller supplies `body` and friends. A silent
// overwrite either way would lose one of them without a word — and the PHP union
// operator used where a merge was meant has already cost this layer two defects,
// one of which left COUNT("hello") answering 1.
//
// The disjointness was true by inspection and enforced by nothing. This is the
// assertion that comment was standing in for. A collision is a bug in the
// translator rather than in a rule or a map, so it throws Error like every other
// startup mistake, not SqlError.
function slots(...maps) {
  const out = {};
  for (const m of maps) {
    for (const [k, v] of Object.entries(m)) {
      if (Object.hasOwn(out, k)) {
        throw new Error(`two sources both supply the skeleton slot {${k}}; one would `
          + 'silently shadow the other');
      }
      out[k] = v;
    }
  }
  return out;
}
