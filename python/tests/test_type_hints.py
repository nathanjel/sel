"""The package ships py.typed, so its annotations are public API. A class that
defines a method named after a builtin type (`Value.int`, `Value.bool`,
`Value.list`, `Args.int` ...) shadows that builtin for every later annotation in
its body: a type checker reads `-> int` there as the method, which is not a type,
and a user's checker sees `Any` (mypy: `[valid-type]`). Such annotations spell
`builtins.int`. This is the check that needs no type checker installed."""
import ast
import builtins
from pathlib import Path

import pytest

SEL = Path(__file__).resolve().parents[1] / 'sel'
TYPES = {n for n in dir(builtins) if isinstance(getattr(builtins, n), type)}


def annotation_nodes(fn):
    a = fn.args
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg):
        if arg is not None and arg.annotation is not None:
            yield arg.annotation
    if fn.returns is not None:
        yield fn.returns


def shadowed_reads(tree):
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        defined = set()
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for ann in annotation_nodes(item):
                    for n in ast.walk(ann):
                        if isinstance(n, ast.Name) and n.id in defined:
                            yield f'{cls.name}.{item.name}:{item.lineno} reads {n.id}'
                if item.name in TYPES:
                    defined.add(item.name)


@pytest.mark.parametrize('path', sorted(SEL.rglob('*.py')), ids=lambda p: str(p.relative_to(SEL)))
def test_no_annotation_names_a_method_that_shadows_a_builtin_type(path):
    tree = ast.parse(path.read_bytes().decode('utf-8'))
    assert list(shadowed_reads(tree)) == []
