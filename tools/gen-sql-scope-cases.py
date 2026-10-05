#!/usr/bin/env python3
"""Generates sql/cases/48-scope-and-slots.sqlt (T08).

Every expectation in it is DERIVED, never copied from the translator under test:

  * a scoping case is compared with its ALPHA-EQUIVALENT CONTROL -- the same
    program with the binder that would (wrongly) capture a name renamed, or with
    an outer `_K` written out as the literal it stands for. Renaming a binder does
    not change what a program means, so the control's translation IS the
    translation the capturing program must produce. The control is also written
    out as a case of its own, so a host that gets the control wrong is
    distinguishable from one that gets the capture wrong.

  * the derivation is done by a reference translator (the JS host,
    tools/sql-scope-ref.mjs). Every host is then held to it by its own sqlt
    runner, and because each control is a case of its own, a host that
    disagrees with the reference on the control is told apart from one that
    gets the capture wrong. `--check` regenerates and compares the file; it is a
    group of tools/check-generated.sh.

Usage:
    python3 tools/gen-sql-scope-cases.py            # rewrite sql/cases/48-...
    python3 tools/gen-sql-scope-cases.py --check    # fail if it is stale
"""
import json, os, subprocess, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_lib  # noqa: E402
CHECK = gen_lib.gen_args('gen-sql-scope-cases', '''usage: python3 tools/gen-sql-scope-cases.py            rewrite sql/cases/48-scope-and-slots.sqlt
       python3 tools/gen-sql-scope-cases.py --check    fail if it is stale''')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'sql/cases/48-scope-and-slots.sqlt')

def col(name, table='t', typ='NUM'):
    return {'kind': 'column', 'table': table, 'column': name, 'type': typ}

A, B, X, Y = ({n: col(n.lower())} for n in 'ABXY')
def merge(*ds):
    out = {}
    for d in ds: out.update(d)
    return out
VCOLS = {'V': {'kind': 'columns', 'items': [
    {'table': 'x', 'column': 'a', 'type': 'NUM'}, {'table': 'x', 'column': 'b', 'type': 'NUM'}]}}
ORDERS = {'ORDERS': {'kind': 'relation', 'from': 'orders', 'alias': 'o',
                     'fields': {'ID': {'kind': 'column', 'table': 'o', 'column': 'id', 'type': 'NUM'}}}}
ITEMS = {'ITEMS': {'kind': 'relation', 'from': 'items', 'alias': 'i', 'fields': {
    'DEPT': {'kind': 'column', 'table': 'i', 'column': 'dept', 'type': 'TEXT'},
    'QTY': {'kind': 'column', 'table': 'i', 'column': 'qty', 'type': 'NUM'}}}}

# (name, note, bindings, program, control-or-None, extras)
#   control None  -> `expect` in extras (error / literal)
CASES = []
def case(name, note, bindings, program, control=None, **extra):
    CASES.append(dict(name=name, note=note, bindings=bindings, program=program,
                      control=control, **extra))

# ---- lexical capture in nested aggregates (JS-C7, PY-C18, PHP-C29) -------------
case('agg.scope.outer-key-in-inner-list',
     "A static list's elements are evaluated in the scope the list is written in: "
     "`_K` inside the inner list is the OUTER element's key, \"1\" then \"2\", and "
     "the outer aggregate unrolls twice. The translation used the inner key in "
     "both halves and was constantly FALSE where SEL says TRUE (JS-C7, PY-C18). "
     "Expected: the outer unroll written out by hand.",
     {}, 'ANY((0,0), ALL((_K, 5), I, I > 1))',
     'ALL(("1", 5), I, I > 1) OR ALL(("2", 5), I, I > 1)')
case('agg.scope.element-named-like-inner-binder',
     "The inner binder A shadows the column A INSIDE the inner body only. The "
     "outer element `A` is the column, and the body reads the outer binder I, so "
     "the inner body is the outer element compared with 0, twice. Expected: the "
     "program with the inner binder renamed (JS-C7).",
     A, 'ALL((A, 1), I, ALL((5, 6), A, I > 0))',
     'ALL((A, 1), I, ALL((5, 6), B, I > 0))')
case('agg.scope.default-binder-nested-static',
     "Both aggregates use the default binder `_`. The inner list's `_` is the "
     "OUTER element; the inner body's `_` is the inner one. It used to be refused "
     "as E_SQL_DEPTH ('the evaluator answers E_DEPTH'), which is false: SEL "
     "evaluates it (JS-C7, PHP-C29). Expected: distinct binder names.",
     A, 'ANY((A, 2), ALL((_, 5), _ > 0))',
     'ANY((A, 2), P, ALL((P, 5), Q, Q > 0))')
case('agg.scope.default-binder-nested-columns',
     "The same over a `columns` binding.", VCOLS, 'ANY(V, ALL((_, 5), _ > 0))',
     'ANY(V, P, ALL((P, 5), Q, Q > 0))')
case('agg.scope.one-name-for-list-element-and-binder',
     "`X` is the outer element in the inner list and then the inner binder: the "
     "name means two different things on the two sides of the comma.",
     VCOLS, 'ANY(V, X, ALL((X, 5), X, X > 0))',
     'ANY(V, P, ALL((P, 5), Q, Q > 0))')
case('agg.scope.outer-key-in-named-binder-body',
     "PHP-C29: `_K` in an inner static list under a NAMED outer binder is still "
     "the outer element's key.",
     {}, 'ANY(("a","b"), o, ANY((_K, "zz"), c, c $== "2"))',
     'ANY(("1","zz"), c, c $== "2") OR ANY(("2","zz"), c, c $== "2")')
case('agg.scope.outer-key-compared-in-inner-body',
     "The inner BODY reads the INNER key: `_K` is the innermost aggregate's key "
     "(SEL: ALL((\"a\",\"b\"), o, ANY((_K, \"zz\"), c, c $== _K)) is FALSE), "
     "while the outer key in the inner LIST is still the outer one.",
     {}, 'ALL(("a","b"), o, ANY((_K, "zz"), c, c $== _K))',
     'ANY(("1","zz"), c, c $== _K) AND ANY(("2","zz"), c, c $== _K)')
case('agg.scope.outer-key-in-join-source',
     "`JOIN` over a static list whose element is the outer key.",
     {}, 'ANY(("a","b"), o, JOIN((_K, "x"), "-") $== "2-x")',
     'JOIN(("1","x"), "-") $== "2-x" OR JOIN(("2","x"), "-") $== "2-x"')
case('agg.scope.binder-named-like-a-column-in-its-own-list',
     "`X` in the list is the COLUMN (the binder is not in scope until the body); "
     "in the body it is the element. Refused as E_SQL_DEPTH before (PHP-C29).",
     X, 'ANY((X, 2), X, X > 1)', 'ANY((X, 2), Z, Z > 1)')

# ---- FILTER binders (PHP-C30, LISP-C7) -----------------------------------------
case('agg.filter.binder-does-not-leak-into-body',
     "The FILTER's binder is local to its predicate. The aggregate body's free "
     "`X` is the column, not the FILTER element (PHP-C30: `x` was rendered as the "
     "element and the column never appeared).",
     X, 'ALL(FILTER((1,2,3), x, x > 1), y, y < X)',
     'ALL(FILTER((1,2,3), w, w > 1), y, y < X)')
case('agg.filter.binder-does-not-leak-into-body-columns',
     "LISP-C7: the same over a `columns` binding, with the body reading a column.",
     merge(VCOLS, X), 'ANY(FILTER(V, x, x > 1), q, q > X)',
     'ANY(FILTER(V, w, w > 1), q, q > X)')
case('agg.filter.binder-is-undefined-in-body',
     "SEL raises E_UNDEF_VAR for `a` in the body (the FILTER's binder is not in "
     "scope there); the translator raises E_SQL_UNBOUND at the same node, not "
     "a translation (LISP-C7).",
     VCOLS, 'ANY(FILTER(V, a, a > 1), q, a < 9)', None,
     error='E_SQL_UNBOUND @a < 9')
case('agg.filter.later-binder-is-undefined-in-earlier-predicate',
     "PHP-C30: `y` is the aggregate's binder, not yet in scope in the FILTER's "
     "predicate.",
     {}, 'ALL(FILTER((1,2,3), x, y > 0), y, y < 9)', None,
     error='E_SQL_UNBOUND @y > 0')
case('agg.filter.outer-filter-binder-is-undefined-in-inner-predicate',
     "LISP-C7: nested FILTERs; `b` belongs to the outer FILTER.",
     VCOLS, 'ANY(FILTER(FILTER(V, a, b > 1), b, a < 9), q, q > 0)', None,
     error='E_SQL_UNBOUND @b > 1')

# ---- helper definitions are not captured by binders (JS-C26, CPP-C23) ----------
case('norm.inline.def-not-captured-by-binder',
     "`X2 = A` is written where `A` is the column; the later binder named A must "
     "not capture it when X2 is inlined (JS-C26). Expected: the binder renamed.",
     A, 'X2 = A; ALL((5,6), A, X2 > 0)', 'X2 = A; ALL((5,6), B, X2 > 0)')
case('norm.inline.def-not-captured-by-binder-arithmetic',
     "CPP-C23: `X = Y + 1; ALL((1,2,3), Y, Y > X)` compared 1 with 1+1 -- a constant "
     "FALSE -- where the column was meant.",
     Y, 'X = Y + 1; ALL((1,2,3), Y, Y > X)', 'X = Y + 1; ALL((1,2,3), Z, Z > X)')
case('norm.inline.binder-in-own-list-element',
     "`ANY((B + 1, 2), B, B > 0)`: the `B` in the list is the column. It recursed "
     "until E_SQL_DEPTH before (CPP-C23, PY-C18).",
     B, 'ANY((B + 1, 2), B, B > 0)', 'ANY((B + 1, 2), C, C > 0)')

# ---- a binder that reuses the name of a `value` binding (JS-C53, PY-C45) --------
VAL5 = {'V': {'kind': 'value', 'value': '5', 'type': 'NUM'}}
VALABC = {'V': {'kind': 'value', 'value': 'abc'}}
ACOL = {'A': {'kind': 'column', 'table': 't', 'column': 'a'}}
case('const.binder-shadows-value-binding',
     "A binder named V is the element, not the scalar binding V. It was treated "
     "as the constant and the column was left unguarded (JS-C53).",
     merge(VAL5, ACOL), 'ALL((A, A), V, V + 1 > 0)', 'ALL((A, A), W, W + 1 > 0)')
case('const.binder-shadows-value-binding-that-would-fail',
     "With V = \"abc\" the binding would make `V + 1` E_SQL_INVALID; the binder "
     "shadows it, so the program translates as it evaluates.",
     merge(VALABC, ACOL), 'ALL((A, 2), V, V + 1 > 0)', 'ALL((A, 2), W, W + 1 > 0)')
QTYREL = {'R': {'kind': 'relation', 'from': 'r', 'alias': 'r', 'fields': {
    'QTY': {'kind': 'column', 'table': 'r', 'column': 'qty', 'type': 'NUM'}}}}
case('const.binder-shadows-value-binding-over-relation',
     "PY-C45: `ANY(R, P, P[\"QTY\"] > 0)` with a value binding P.",
     merge(QTYREL, {'P': {'kind': 'value', 'value': 'abc'}}),
     'ANY(R, P, P["QTY"] > 0)', 'ANY(R, Q, Q["QTY"] > 0)')

# ---- assignment: indexed lists are copies, not aliases (GO-C4) ------------------
case('assign.indexed.copy-is-not-alias',
     "`X = R` copies (spec 3.4): the later write to R is not in X. COUNT(X) is 1 "
     "in SEL; the translators saw 2 (GO-C4).",
     {}, 'R[1] = 5; X = R; R[2] = 6; COUNT(X)', 'R[1] = 5; X = R; COUNT(X)')
case('assign.indexed.copy-is-not-alias-source-unchanged',
     "And a write through the copy does not reach the source.",
     {}, 'R[1] = 5; X = R; X[2] = 7; COUNT(R)', 'R[1] = 5; COUNT(R)')
case('assign.indexed.copy-is-not-alias-columns',
     "The same over columns: X holds one element, not two.",
     merge(A, B), 'R[1] = A; X = R; R[2] = B; SUM(X, _ + 1)',
     'R[1] = A; X = R; SUM(X, _ + 1)')

# ---- a relation name rebound by a helper (PHP-C34) ------------------------------
case('plan.helper.rebinds-relation-once',
     "`ORDERS = ORDERS .> DROP(2)` reads the ORDERS binding once; the later "
     "`ORDERS .> TAKE(3)` reads the helper. The pipeline is DROP 2 then TAKE 3 "
     "(OFFSET 2 LIMIT 3), not DROP applied twice (PHP-C34).",
     ORDERS, 'ORDERS = ORDERS .> DROP(2); ORDERS .> TAKE(3)',
     'ORDERS .> DROP(2) .> TAKE(3)', plan=True)

# ---- what a static FILTER may feed (LISP-C6) ------------------------------------
case('agg.join.filtered-source-is-refused',
     "FILTER yields a list; only ALL, ANY, SUM and COUNT absorb it (docs/internals/"
     "sql-translation.md 7.5), so JOIN over one is E_SQL_SHAPE at the FILTER. Every "
     "host dropped the FILTER and joined the whole list (LISP-C6).",
     {}, 'JOIN(FILTER(("a","b","c"), _ $== "a"), ",")', None,
     error='E_SQL_SHAPE @FILTER(')
case('agg.join.filtered-source-is-refused-constant-false',
     "Even a predicate that is constantly FALSE: the answer is \"\", not \"a,b,c\".",
     {}, 'JOIN(FILTER(("a","b","c"), FALSE), ",")', None,
     error='E_SQL_SHAPE @FILTER(')
case('agg.filter.absorbed-into-any-control',
     "Control: the four absorbed shapes still translate.",
     {}, 'ANY(FILTER(("a","b"), _ $== "a"), _ $== "a")',
     'ANY(("a","b"), _ $== "a" AND _ $== "a")')

# ---- non-name binder argument (GO-C2) -------------------------------------------
case('shape.binder.non-name-binder-is-refused',
     "A binder position holding an index expression is refused (E_SQL_SHAPE at "
     "the index), never a nil dereference (GO-C2).",
     A, 'BUCKET("x", A[1], "y", 1)', None, error='E_SQL_SHAPE @[1]')

# ---- deep helper chains and deep pipelines (JS-C54, CPP-C10, CPP-C17) -----------
COLN = {'N': col('n')}
def chain(n, base='N'):
    return ''.join('X%d = %s; ' % (i, base if i == 0 else 'X%d + 1' % (i - 1))
                   for i in range(n)) + 'X%d > 0' % (n - 1)
case('limit.inline-chain-past-the-depth-limit-over-a-column',
     "250 chained helpers over a column inline to an expression 250 deep: past "
     "the evaluator's own MAX_DEPTH (errors.md, E_SQL_DEPTH), so a refusal with "
     "that code -- never a host stack overflow (CPP-C10: SIGSEGV at 60000; "
     "JS-C54: RangeError at 20000).",
     COLN, chain(250), None, error='E_SQL_DEPTH')
case('limit.inline-chain-past-the-depth-limit-over-a-constant',
     "The same over a constant. It was E_SQL_INVALID ('SEL rejects this "
     "expression (E_DEPTH)'), which blames SEL for evaluating something SEL "
     "evaluates fine; the refusal is about the translated expression's nesting, "
     "the E_SQL_DEPTH the errors registry describes (JS-C54 d).",
     {}, chain(250, '1'), None, error='E_SQL_DEPTH')
case('limit.inline-chain-just-under-the-depth-limit',
     "A chain that stays inside the limit still translates (control).",
     COLN, chain(150), chain(150))

RAW = []   # (name, note, bindings, source, plan, tables, expect)
RAW.append(('plan.pure-memory.pipeline-past-the-depth-limit',
            "250 pipe steps: stage 1 refuses a program past the depth limit, so the plan is "
            "pure_memory and never an exception (JS-C54 c: RangeError at 20000).",
            ORDERS, 'ORDERS' + ' .> TAKE(1)' * 250, 'pure_memory', 'orders', None))
RAW.append(('plan.pure-sql.pipeline-under-the-depth-limit',
            "150 pipe steps still push down (control).",
            ORDERS, 'ORDERS' + ' .> TAKE(1)' * 150, 'pure_sql', 'orders',
            'SELECT `o`.* FROM `orders` `o` LIMIT 1'))

# ---- non-name binder in the statement forms (GO-C2) -----------------------------
STMT_BINDER = [
    ('shape.binder.non-name-binder-in-map-statement',
     'ITEMS .> MAP(BUCKET("x", _["DEPT"], "x", _["QTY"]))', '["DEPT"]'),
    ('shape.binder.non-name-binder-in-bucket-projection-statement',
     'ITEMS .> BUCKET(_["DEPT"]) .> MAP(BUCKET("entity", COUNT(_), "latest", '
     'TOP_BY(_, _["QTY"], "DESC", 1)))', 'COUNT(_)'),
]

# ---- the expansion budget: E_SQL_SIZE (MAX_SQL_NODES = 250 000) -------------------
# Sizes are derived (docs/internals/sql-translation.md 7.4): with a column base the
# doubling program `X0 = N; Xi = X(i-1) + X(i-1); Xk > 0` costs 2^(k+1)+1 nodes and
# the nested `ALL((A,A), V1, ALL((V1,V1), V2, ... Vn > 0))` costs 2^(n+2)-1. Each
# refusal is at least twice the limit and each acceptance at most half of it (except
# the constant/column pair, which is the same program), so a host that counts a few
# nodes differently cannot move a verdict.
def doubling(k, base):
    return ''.join('X%d = %s; ' % (i, base if i == 0 else 'X%d + X%d' % (i - 1, i - 1))
                   for i in range(k + 1)) + 'X%d > 0' % k
def nested(n):
    s = 'V%d > 0' % n
    for i in range(n, 0, -1):
        src = ('A', 'A') if i == 1 else ('V%d' % (i - 1),) * 2
        s = 'ALL((%s,%s), V%d, %s)' % (src[0], src[1], i, s)
    return s
case('limit.size.doubling-helper-over-a-column',
     "19 statements: `X18 > 0` is 2^19+1 = 524 289 nodes once every read of a helper is "
     "expanded, over twice MAX_SQL_NODES. Refused with E_SQL_SIZE, quickly: the walk stops "
     "at the first node over the limit (JS-C8, PHP-C32, PY-C6, CPP-C17, LISP-C23, GO-C20).",
     COLN, doubling(18, 'N'), None, error='E_SQL_SIZE')
case('limit.size.doubling-helper-over-a-constant',
     "The same over a literal. The count is taken before constant folding hands a subtree "
     "to SEL's evaluator, so this is the same refusal and not a slow answer.",
     {}, doubling(18, '1'), None, error='E_SQL_SIZE')
case('limit.size.nested-aggregates-over-static-lists',
     "17 nested `ALL((V,V), W, ...)` levels: two unrolled elements per level, so the "
     "innermost body is instantiated 2^16 times and the expression is 2^19-1 nodes.",
     A, nested(17), None, error='E_SQL_SIZE')
case('limit.size.small-doubling-is-accepted',
     "Control: the same shape at k = 3 (17 nodes) translates.",
     COLN, doubling(3, 'N'), doubling(3, 'N'))
case('limit.size.small-nested-is-accepted',
     "Control: the nested shape at n = 4 (63 nodes) translates.",
     A, nested(4), nested(4))

# ---- BUCKET SUM bodies with literals (PHP-C7, PY-C5, GO-C3) ---------------------
# The expected statement is the known-good `SUM(_["QTY"])` statement with the
# body swapped for the rendering the SAME body has as a WHERE clause on the same
# relation (a body is one expression; where it stands does not change how it is
# written).
BUCKET_SQL = 'ITEMS .> BUCKET(_["DEPT"], RECORD("k", _K, "s", SUM(_, %s)))'
FILTER_SQL = 'ITEMS .> FILTER(%s > 0)'
BODIES = [
    ('numeric-literal', '_["QTY"] * 2', 'inline'),
    ('two-numeric-literals', '_["QTY"] * 7 + 5', 'inline'),
    ('text-literal-in-if', 'IF(_["DEPT"] $== "x", 1, 2)', 'inline'),
    ('text-literal-in-if-params', 'IF(_["DEPT"] $== "x", 1, 2)', 'params'),
    ('nested-if-two-text-slots',
     'IF(_["DEPT"] $== "x", IF(_["DEPT"] $== "y", 3, 4), 5)', 'inline'),
    ('nested-if-two-text-slots-params',
     'IF(_["DEPT"] $== "x", IF(_["DEPT"] $== "y", 3, 4), 5)', 'params'),
]

def render(spec):
    """Reference translation (JS host) of a spec; a string, or 'ERR code l:c'."""
    out = subprocess.run(['node', os.path.join(ROOT, 'tools/sql-scope-ref.mjs')],
                         input=json.dumps(spec), capture_output=True, text=True, cwd=ROOT)
    if out.returncode != 0:
        raise SystemExit(out.stderr)
    return json.loads(out.stdout)

def locate(program, marker):
    i = program.index(marker)
    return '1:%d' % (i + 1)

def emit():
    lines = ['# Scope, literal slots and expansion (T08, worklist 2026-09-29).',
             '#',
             '# GENERATED by tools/gen-sql-scope-cases.py -- do not edit. Every expected',
             '# string is derived, not observed: a scoping case equals the translation of its',
             '# alpha-equivalent control (a binder renamed, an outer `_K` written out), and a',
             '# BUCKET SUM case equals the known-good statement with the body\'s own',
             '# rendering swapped in. tools/gen-sql-scope-cases.py --check regenerates and',
             '# compares. Each capture case is followed by its control, which must pass',
             '# everywhere; a host that fails only the capture case has a scoping bug.',
             '']
    def block(name, note, dialect, bindings, source, expect=None, error=None,
              mode=None, params=None, as_=None, plan=None, tables=None):
        b = ['### name: ' + name]
        if note:
            b += ['--- note', note]
        b += ['--- dialect', dialect]
        b += ['--- bindings', json.dumps(bindings, separators=(', ', ': '))]
        if mode: b += ['--- mode', mode]
        if as_: b += ['--- as', as_]
        b += ['--- source', source]
        if plan:
            b += ['--- plan', plan, '--- tables', tables]
        if error: b += ['--- error', error]
        else: b += ['--- expect', expect]
        if params is not None: b += ['--- params', params]
        b += ['===', '']
        return b
    for c in CASES:
        plan = c.get('plan')
        if c['control'] and c['control'] == c['program']:
            got = render(dict(src=c['control'], bindings=c['bindings'], as_='value'))
            assert not got.startswith(('ERR', 'HOST')), (c['name'], got)
            lines += block(c['name'], c['note'], 'mariadb', c['bindings'], c['program'], expect=got)
        elif c['control']:
            spec = dict(src=c['control'], bindings=c['bindings'], as_='plan' if plan else 'value')
            got = render(spec)
            if plan:
                assert got.startswith('PLAN '), (c['name'], got)
                cls, tables, sql = [x.strip() for x in got[5:].split('|', 2)]
                lines += block(c['name'], c['note'], 'mariadb', c['bindings'], c['program'],
                               expect=sql, plan=cls, tables='\n'.join(json.loads(tables)))
                lines += block(c['name'] + '-control', 'The same statement written without the rebinding.',
                               'mariadb', c['bindings'], c['control'], expect=sql,
                               plan=cls, tables='\n'.join(json.loads(tables)))
            else:
                assert not got.startswith(('ERR', 'HOST')), (c['name'], got)
                lines += block(c['name'], c['note'], 'mariadb', c['bindings'], c['program'], expect=got)
                lines += block(c['name'] + '-control', 'Alpha-equivalent control for ' + c['name'] + '.',
                               'mariadb', c['bindings'], c['control'], expect=got)
        else:
            code, _, marker = c['error'].partition(' @')
            lines += block(c['name'], c['note'], 'mariadb', c['bindings'], c['program'],
                           error=('%s %s' % (code, locate(c['program'], marker))) if marker else code)
    for name, src, mk in STMT_BINDER:
        lines += block(name, 'GO-C2: a binder position holding something that is not a name is '
                       'E_SQL_SHAPE at that expression, in the statement forms too, never a crash '
                       'or a different code.', 'mariadb', ITEMS, src,
                       error='E_SQL_SHAPE %s' % locate(src, mk), as_='statement')
    for name, note, b, src, cls, tables, expect in RAW:
        blk = ['### name: ' + name, '--- note', note, '--- dialect', 'mariadb', '--- bindings',
               json.dumps(b, separators=(', ', ': ')), '--- source', src, '--- plan', cls,
               '--- tables', tables]
        if expect: blk += ['--- expect', expect]
        blk += ['===', '']
        lines += blk
    # BUCKET SUM
    s0 = render(dict(src=BUCKET_SQL % '_["QTY"]', bindings=ITEMS, as_='statement'))
    for name, body, mode in BODIES:
        ctl = render(dict(src=FILTER_SQL % body, bindings=ITEMS, as_='statement', mode=mode))
        assert ctl.startswith('SELECT `i`.* FROM `items` `i` WHERE (') and ctl.endswith(' > 0)'), ctl
        expr = ctl[len('SELECT `i`.* FROM `items` `i` WHERE ('):-len(' > 0)')]
        want = s0.replace('SUM(`i`.`qty`)', 'SUM(%s)' % expr)
        assert want != s0
        params = None
        if mode == 'params':
            pr = render(dict(src=FILTER_SQL % body, bindings=ITEMS, as_='params-of-statement'))
            params = pr
        lines += block('bucket.sum.' + name,
                       'PHP-C7, PY-C5, GO-C3: the body of SUM inside a BUCKET projection keeps its '
                       'literals. Derived from the WHERE-clause rendering of the same body.',
                       'mariadb', ITEMS, BUCKET_SQL % body, expect=want, mode=None if mode == 'inline' else mode,
                       as_='statement', params=params)
    return '\n'.join(lines).rstrip('\n') + '\n'

if __name__ == '__main__':
    gen_lib.write_or_check('gen-sql-scope-cases', ROOT, [(os.path.relpath(OUT, ROOT), emit())],
                           CHECK, 'python3 tools/gen-sql-scope-cases.py')
