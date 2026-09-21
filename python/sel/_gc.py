"""Pause the cyclic collector while a program runs.

CPython's collector triggers on allocation counts, and each pass walks every
object in the generations it collects: a full pass walks the whole resident
context. A query that builds one row per element over a large context trips
those passes constantly and they find nothing, because SEL values are trees --
a row never refers back to anything that refers to it. Reference counting
frees a run's garbage regardless of the collector, so pausing it for the
run defers no memory; it removes pure overhead (WL-001 SEL-0030: 27--40% of
the join-heavy scenarios' time was collector wall time). The one cycle a run
can make is not in evaluation: rebuilding the physical tree for a new context
compiles math plans, whose nested closures form about ten cyclic objects per
plan. They are reclaimed at the collector's first pass after the run.

Re-entrant (a builtin may run a program), exception-safe, and a no-op when the
application has the collector off already; the collector is handed back exactly
as it was found. The state it touches is process-global, so a program that
runs for long in one thread delays cycle collection for the others by that
long -- a query's duration.
"""
import gc

_depth = 0
_resume = False


class bulk_allocation:
    __slots__ = ()

    def __enter__(self):
        global _depth, _resume
        if _depth == 0:
            _resume = gc.isenabled()
            if _resume:
                gc.disable()
        _depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        global _depth
        _depth -= 1
        if _depth == 0 and _resume:
            gc.enable()
        return False
