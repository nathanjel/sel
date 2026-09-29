"""A guaranteed recursion budget for the entry points that walk a tree.

spec/SPEC.md 6.4 makes E_DEPTH -- or a value -- the only outcome for a program
that nests too deeply, and caps the nesting at 200. CPython counts every Python
frame against `sys.getrecursionlimit()` (1000 by default), and this host spends
several frames per level of nesting: about six for a pipeline step, five for a
call chain, three for each level of a value. A program well under the cap
therefore exhausted the interpreter's limit first and a RecursionError, which
is not a SEL error at all, escaped where every other host answered.

The fix is not to convert RecursionError into E_DEPTH -- that would report a
position it does not have, and would turn a legal program into an error the
other hosts do not raise. It is to make sure the limit is never the thing that
decides: an entry point asks for a budget large enough for MAX_DEPTH levels at
the widest per-level cost this host has, counted from where the caller already
is, and gives it back on the way out.

`sys.setrecursionlimit` is process-global, so the state is guarded by a lock:
concurrent entries share one raise, the largest asked for, and the last one out
restores what was there before -- unless the application changed the limit in
the meantime, which is then left alone.
"""
import sys
import threading

from ._limits import MAX_DEPTH

# Frames for one level of nesting at the widest this host spends (a binding
# aggregate inside a pipeline), rounded up, plus headroom for the entry point,
# a registered host function's own frames and the caller's callbacks.
FRAMES_PER_LEVEL = 12
HEADROOM = 400
BUDGET = FRAMES_PER_LEVEL * MAX_DEPTH + HEADROOM

_lock = threading.Lock()
_holders = 0
_before = 0          # the limit when the first holder arrived
_ours = 0            # the limit we last set, to notice an application's own change


def _frames_below() -> int:
    """How deep the calling thread already is."""
    n = 0
    f = sys._getframe(1)
    while f is not None:
        n += 1
        f = f.f_back
    return n


class recursion_budget:
    __slots__ = ()

    def __enter__(self):
        global _holders, _before, _ours
        need = _frames_below() + BUDGET
        with _lock:
            current = sys.getrecursionlimit()
            if _holders == 0:
                _before = current
            _holders += 1
            if current < need:
                sys.setrecursionlimit(need)
                _ours = need
        return self

    def __exit__(self, exc_type, exc, tb):
        global _holders
        with _lock:
            _holders -= 1
            if _holders == 0 and _ours and sys.getrecursionlimit() == _ours:
                # Never below what this very thread is using, or the call fails.
                sys.setrecursionlimit(max(_before, _frames_below() + 50))
        return False
