"""Every built-in module is imported here for its side effect: each registers
its functions in the table in sel/registry.py.

The table must be complete before any source is parsed, because an unknown
function name is a compile-time error. sel/__init__.py imports this module
first, for exactly that reason.
"""

from . import control      # noqa: F401
from . import structure    # noqa: F401
from . import aggregate    # noqa: F401
from . import text         # noqa: F401
from . import number       # noqa: F401
from . import binary       # noqa: F401
from . import regex        # noqa: F401
from . import null         # noqa: F401
