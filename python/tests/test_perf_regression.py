"""PY-REG-1: recovering the 0.9.2 scale-test time without changing an answer.

Three changes, each checked against the plain (unoptimised) tree and against
what it must not do: FILTER hands its kept elements to a step that only reads
them (and copies whatever it collects); RECORD skips the copy of a computed
scalar; Value.clone copies a leaf without the recursive call."""
import pytest
import sel
from sel import registry
from sel.errors import MAX_DEPTH, SelError
from sel.eval import Context, eval_node
from sel.value import Value


def plain(program, ctx):
    return eval_node(program.ast, Context(Value.from_native(ctx)))


def physical(program, ctx):
    return program.run(Value.from_native(ctx))


def filter_steps(node, out=None):
    out = [] if out is None else out
    if node is None:
        return out
    if node.t == 'call' and node.name == 'FILTER':
        out.append(node)
    for child in [*node.args, *node.items, node.l, node.r, node.x, node.obj, node.idx, node.value]:
        if child is not None:
            filter_steps(child, out)
    return out


ROWS = {'L': [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}, {'a': 3, 'b': 'x'}]}


@pytest.mark.parametrize('consumer,adopts', [
    ('MAP(RECORD("k", _["a"]))', True),
    ('MAP(_["a"] * 2)', True),
    ('BUCKET(_["b"])', False),           # keeps members together: it copies them itself
    ('SORT_BY(_["a"])', False),          # a sort keeps the elements themselves
    ('TAKE(2)', False),
    ('LINK(L, _1["a"] == _2["a"])', False),
    ('MAP((L[2]["a"] = 5; _))', False),  # the body assigns to a variable the rows came from
])
def test_filter_hands_elements_on_only_to_a_step_that_only_reads(consumer, adopts):
    program = sel.compile(f'L .> FILTER(_["a"] > 0) .> {consumer}')
    program.run(Value.from_native(ROWS))
    steps = filter_steps(program.physical_ast())
    assert [s.adopt_items for s in steps] == [adopts]


def test_last_step_keeps_its_copy():
    program = sel.compile('L .> FILTER(_["a"] > 0)')
    program.run(Value.from_native(ROWS))
    assert [s.adopt_items for s in filter_steps(program.physical_ast())] == [False]


def test_a_host_function_in_the_body_keeps_the_copy():
    registry.register_function('PERF_REG_POKE', 1, 1, lambda args: args.val(0))
    try:
        program = sel.compile('L .> FILTER(_["a"] > 0) .> MAP(PERF_REG_POKE(_))')
        program.run(Value.from_native(ROWS))
        assert [s.adopt_items for s in filter_steps(program.physical_ast())] == [False]
    finally:
        registry._table.pop('PERF_REG_POKE')
        registry._host.discard('PERF_REG_POKE')


@pytest.mark.parametrize('source', [
    'L .> FILTER(_["a"] > 1) .> MAP(RECORD("r", _, "n", _["a"] * 2))',
    'L .> FILTER(_["b"] $== "x") .> MAP(_)',
    'SUM(L .> FILTER(_["a"] > 1), _["a"])',
    'L .> FILTER(_["a"] > 1) .> MAP(_["a"] * 2)',
    'L .> FILTER(_["a"] >= 1) .> FILTER(_["b"] $== "x") .> MAP(RECORD("k", _["a"]))',
])
def test_plain_and_optimised_agree(source):
    program = sel.compile(source)
    assert physical(program, ROWS).dump() == plain(program, ROWS).dump()


def test_result_is_independent_of_the_source_after_adoption():
    # The elements FILTER handed on were only read; what MAP collected is a copy.
    ctx = Value.from_native(ROWS)
    program = sel.compile('X = L .> FILTER(_["a"] > 1) .> MAP(RECORD("r", _)); X[1]["r"]["a"] = 99; L[2]["a"]')
    assert program.run(ctx).scalar == '2'
    assert ctx.get('L').values()[1].get('a').scalar == '2'


def test_a_body_that_assigns_still_sees_the_rows_as_filter_kept_them():
    ctx = Value.from_native(ROWS)
    # The body overwrites a later source row while MAP is reading the kept ones:
    # MAP must still see the rows as they were when FILTER kept them (a copy).
    program = sel.compile('L .> FILTER(_["a"] > 0) .> MAP((L[2]["a"] = 100; _["a"]))')
    assert [v.scalar for v in program.run(ctx).values()] == ['1', '2', '3']


def nested(levels):
    v = Value.text('x')
    for _ in range(levels):
        w = Value.none()
        w.set('a', v)
        v = w
    return v


@pytest.mark.parametrize('levels,ok', [(MAX_DEPTH - 2, True), (MAX_DEPTH - 1, False), (MAX_DEPTH, False)])
def test_check_depth_refuses_exactly_what_clone_refuses(levels, ok):
    v = nested(levels)            # `levels` bracket levels: nesting depth levels + 1
    clone_ok = check_ok = True
    try:
        v.clone(None, 2)
    except SelError as e:
        assert e.code == 'E_DEPTH'
        clone_ok = False
    try:
        v.check_depth(2, None)
    except SelError as e:
        assert e.code == 'E_DEPTH'
        check_ok = False
    assert clone_ok == check_ok == ok


def test_filter_of_a_too_deep_element_is_refused_with_or_without_adoption():
    deep = nested(MAX_DEPTH - 1)
    ctx = Value.none()
    ctx.set('L', Value._list_owned([deep]))
    for source in ('L .> FILTER(TRUE) .> COUNT()', 'L .> FILTER(TRUE) .> MAP(1) .> COUNT()'):
        program = sel.compile(source)
        with pytest.raises(SelError) as plain_err:
            eval_node(program.ast, Context(ctx))
        with pytest.raises(SelError) as phys_err:
            program.run(ctx)
        assert (plain_err.value.code, plain_err.value.line, plain_err.value.col) == \
               (phys_err.value.code, phys_err.value.line, phys_err.value.col)


def test_record_copies_an_aliased_argument_but_not_a_computed_scalar():
    ctx = Value.from_native({'C': {'x': 'v'}})
    program = sel.compile('R = RECORD("v", C["x"], "n", 1 + 2); R["v"]["k"] = 1; HAS(C["x"], "k")')
    assert program.run(ctx).scalar is False      # the copy, not the context's own leaf, took the child


def test_record_still_bounds_the_nesting_of_a_container_argument():
    deep = nested(MAX_DEPTH - 2)
    ctx = Value.none()
    ctx.set('B', deep)
    with pytest.raises(SelError) as e:
        sel.compile('RECORD("a", LIST(B))').run(ctx)
    assert e.value.code == 'E_DEPTH'


def test_leaf_clone_is_independent_keeps_the_decimal_and_honours_the_cap():
    v = Value.text('12.50')
    v.as_decimal(None)                      # cache the parsed decimal
    c = v.clone()
    assert c is not v and c.scalar == '12.50' and c.kind == v.kind
    c.set('k', Value.text('1'))
    assert v.size() == 0                    # children added to the copy never reach the original
    with pytest.raises(SelError) as e:
        v.clone(None, MAX_DEPTH + 1)
    assert e.value.code == 'E_DEPTH'
    assert v.clone(None, MAX_DEPTH).scalar == '12.50'


def test_args_reads_out_of_range_are_bad_arg_not_index_error():
    registry.register_function('PERF_REG_BADREAD', 0, 1, lambda a: a.val(3))
    registry.register_function('PERF_REG_NEGREAD', 0, 1, lambda a: a.val(-1))
    try:
        for src in ('PERF_REG_BADREAD()', 'PERF_REG_NEGREAD(1)'):
            with pytest.raises(SelError) as e:
                sel.evaluate(src)
            assert e.value.code == 'E_BAD_ARG'
    finally:
        for n in ('PERF_REG_BADREAD', 'PERF_REG_NEGREAD'):
            registry._table.pop(n)
            registry._host.discard(n)


def test_filter_mutation_of_earlier_collected_row_by_later_predicate():
    src = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER((X[1]["k"] = _["k"]; TRUE)) .> MAP(_["k"]))
    '''
    prog = sel.compile(src)
    assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    r1 = plain(prog, ctx1)
    r2 = prog.run(ctx2)
    assert [v.scalar for v in r1.values()] == ['1', '2']
    assert [v.scalar for v in r2.values()] == ['1', '2']
    assert ctx1.dump() == ctx2.dump()


def test_filter_sparse_input_keys_and_explicit_binder():
    src = '''
    X = RECORD("first", RECORD("k", 1), "second", RECORD("k", 2));
    (X .> FILTER(row, (IF(row["k"] == 2, (X["first"]["k"] = 99), 0); TRUE)) .> MAP(row, row["k"]))
    '''
    prog = sel.compile(src)
    assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    r1 = plain(prog, ctx1)
    r2 = prog.run(ctx2)
    assert [v.scalar for v in r1.values()] == ['1', '2']
    assert [v.scalar for v in r2.values()] == ['1', '2']
    assert ctx1.dump() == ctx2.dump()


def test_filter_rejected_later_row_whose_predicate_mutates_earlier_kept_row():
    src = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER((IF(_["k"] == 2, (X[1]["k"] = 99; FALSE), TRUE))) .> MAP(_["k"]))
    '''
    prog = sel.compile(src)
    assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    r1 = plain(prog, ctx1)
    r2 = prog.run(ctx2)
    assert [v.scalar for v in r1.values()] == ['1']
    assert [v.scalar for v in r2.values()] == ['1']
    assert ctx1.dump() == ctx2.dump()


def test_filter_nested_assignments_inside_predicate_calls_or_index():
    src_idx = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER(_[(X[1]["k"] = 99; "k")] > 0) .> MAP(_["k"]))
    '''
    prog_idx = sel.compile(src_idx)
    assert [s.adopt_items for s in filter_steps(prog_idx.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    assert plain(prog_idx, ctx1).dump() == prog_idx.run(ctx2).dump()
    assert ctx1.dump() == ctx2.dump()

    src_call = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER(IF(X[1]["k"] == 1, (X[1]["k"] = 99; TRUE), TRUE)) .> MAP(_["k"]))
    '''
    prog_call = sel.compile(src_call)
    assert [s.adopt_items for s in filter_steps(prog_call.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    assert plain(prog_call, ctx1).dump() == prog_call.run(ctx2).dump()
    assert ctx1.dump() == ctx2.dump()


def test_filter_registered_host_function_mutates_previously_collected_row():
    def mutator(args):
        v = args.val(0)
        v.set('k', Value.text('99'))
        return Value.bool(True)

    registry.register_function('MUTATE_PREV_HOST', 1, 1, mutator)
    try:
        src = '''
        X = LIST(RECORD("k", 1), RECORD("k", 2));
        (X .> FILTER(MUTATE_PREV_HOST(X[1])) .> MAP(_["k"]))
        '''
        prog = sel.compile(src)
        assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
        ctx1 = Value.none()
        ctx2 = Value.none()
        assert plain(prog, ctx1).dump() == prog.run(ctx2).dump()
        assert ctx1.dump() == ctx2.dump()
    finally:
        registry._table.pop('MUTATE_PREV_HOST', None)
        registry._host.discard('MUTATE_PREV_HOST')


def test_filter_application_defined_function_installed_through_lower_level_api():
    def dummy(args, ctx):
        return Value.bool(True)

    registry.define('LOW_LEVEL_PRED', 1, 1, fn=dummy)
    try:
        src = '''
        X = LIST(RECORD("k", 1), RECORD("k", 2));
        (X .> FILTER(LOW_LEVEL_PRED(_["k"])) .> MAP(_["k"]))
        '''
        prog = sel.compile(src)
        assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
        ctx1 = Value.none()
        ctx2 = Value.none()
        assert plain(prog, ctx1).dump() == prog.run(ctx2).dump()
        assert ctx1.dump() == ctx2.dump()
    finally:
        registry._table.pop('LOW_LEVEL_PRED', None)


def test_filter_mutating_map_consumer_disables_elision():
    src = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER(_["k"] > 0) .> MAP((X[1]["k"] = 99; _["k"])))
    '''
    prog = sel.compile(src)
    assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [False]
    ctx1 = Value.none()
    ctx2 = Value.none()
    assert plain(prog, ctx1).dump() == prog.run(ctx2).dump()
    assert ctx1.dump() == ctx2.dump()


def test_filter_read_only_filter_and_map_enables_copy_elision():
    src = '''
    X = LIST(RECORD("k", 1), RECORD("k", 2));
    (X .> FILTER(_["k"] > 0) .> MAP(_["k"] * 2))
    '''
    prog = sel.compile(src)
    assert [s.adopt_items for s in filter_steps(prog.physical_ast())] == [True]
    ctx1 = Value.none()
    ctx2 = Value.none()
    assert plain(prog, ctx1).dump() == prog.run(ctx2).dump()
    assert ctx1.dump() == ctx2.dump()

