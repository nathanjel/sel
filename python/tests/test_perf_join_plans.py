"""PY-P2: compiled join plans are cached by their generated source, and the shape
cache evicts a quarter instead of everything. Results must not change."""
import sel
from sel import value as V
from sel.builtins import structure as S


def layouts(n, distinct):
    a = [{('f%d' % (i % distinct)): i, 'x': i % 5, ('g%d' % (i % 7)): 'v'} for i in range(n)]
    b = [{'bid': j, 'w': str(j)} for j in range(5)]
    return {'A': a, 'B': b}


PROGRAM = 'LINK(A, B, _1["x"] == _2["bid"]) .> MAP(_["w"] & ":" & _["x"]) .> JOIN(",")'


def test_layouts_that_differ_only_in_key_names_share_one_compiled_plan():
    S._PLAN_CODE.clear()
    sel.compile(PROGRAM).run(layouts(400, 400))
    # 400 distinct layouts, but the generated text names indices, not keys.
    assert 0 < len(S._PLAN_CODE) < 20


def test_the_plan_cache_is_bounded():
    for i in range(S._PLAN_CODE_ENTRIES * 3):
        S._plan_code('x = %d' % i)
    assert len(S._PLAN_CODE) <= S._PLAN_CODE_ENTRIES


def test_results_are_unchanged_across_shape_cache_eviction():
    data = layouts(700, 700)          # more layouts than the shape cache holds
    first = sel.compile(PROGRAM).run(data).scalar
    second = sel.compile(PROGRAM).run(data).scalar
    assert first == second
    # an equal record layout built after eviction is an equal, distinct object
    assert len(V._SHAPES) <= V._SHAPE_CACHE_ENTRIES
    keys = tuple('f%d' % i for i in range(3))
    s1 = V._record_shape(keys)
    for i in range(V._SHAPE_CACHE_ENTRIES * 2):
        V._record_shape(('evict%d' % i,))
    s2 = V._record_shape(keys)
    assert s1.keys == s2.keys and s1.key_map == s2.key_map
