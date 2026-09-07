# Goes in the matching python/sel/builtins/*.py, and the name must be added to
# the imports in python/sel/builtins/__init__.py. Not a runnable file.
# EXAMPLE-BEGIN
def _ord_suffix(a, ctx):
    n = a.non_neg_int(0)
    tens = n % 100
    if 11 <= tens <= 13:
        return Value.text(f'{n}th')
    return Value.text(f'{n}' + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th'))


define('ORD_SUFFIX', 1, 1, fn=_ord_suffix)
# EXAMPLE-END
