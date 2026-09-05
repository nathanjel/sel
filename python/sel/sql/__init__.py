"""SEL -> SQL translation.

Not implemented yet. This package currently holds only ``_map.py``, the
generated dialect table (see ``sql/MAP.md``), which is committed so that the
translator can be transcribed against data the PHP host has already agreed on.

The design and the milestone order are in ``docs/SQL-TRANSLATION.md``; the
Python port is M6, after PHP is proven end to end against MariaDB.
"""

from ._map import DIALECTS

__all__ = ['DIALECTS']
