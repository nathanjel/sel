"""tools/join-rows-oracle/model.py -- spec §7.4's joined row, from the text.

An independent model of what `LINK` and `LINK_LEFT` return, written from the
spec and no host, used to generate programs together with the rows they must
answer (SEL-0053). Values:
    ('t', text)          TEXT (a number is text)
    ('null',)            NULL
    ('bool', b)          BOOL
    ('rec', [(k, v)])    a record with at least one field
    ('list', [v])        a list with at least one element
"""
NULL = ('null',)


def dump(v):
    kind = v[0]
    if kind == 't':
        return 't"' + v[1].replace('\\', '\\\\').replace('"', '\\"') + '"'
    if kind == 'null':
        return '-'
    if kind == 'bool':
        return 'TRUE' if v[1] else 'FALSE'
    if kind == 'rec':
        return '-{' + ', '.join(f'"{k}"={dump(x)}' for k, x in v[1]) + '}'
    if kind == 'list':
        return '-{' + ', '.join(f'"{i + 1}"={dump(x)}' for i, x in enumerate(v[1])) + '}'
    raise ValueError(v)


def source(v):
    kind = v[0]
    if kind == 't':
        return v[1] if v[1].lstrip('-').isdigit() else '"' + v[1] + '"'
    if kind == 'null':
        return 'NULL'
    if kind == 'bool':
        return 'TRUE' if v[1] else 'FALSE'
    if kind == 'rec':
        return 'RECORD(' + ', '.join(f'"{k}", {source(x)}' for k, x in v[1]) + ')'
    if kind == 'list':
        return 'LIST(' + ', '.join(source(x) for x in v[1]) + ')'
    raise ValueError(v)


def nested(v):
    """A nested record: a record with a field (a list is not one)."""
    return v[0] == 'rec' and len(v[1]) > 0


def get(rec, key):
    for k, x in rec[1]:
        if k == key:
            return x
    return None


def extend(elem, names):
    """The element as a named argument binds it: extended with the name and
    its lowercase as keys holding the bare element -- the lowercase only where
    the element lacks it -- unless the element already has the name itself,
    in which case it is bound as it is."""
    fields = list(elem[1])
    have = {k for k, _ in fields}
    if not names or names[0] in have:
        return elem
    for n in names:
        if n not in have:
            fields.append((n, elem))
            have.add(n)
    return ('rec', fields)


def binder_names(name):
    """A name binds as written in canonical form (identifiers are ASCII
    case-insensitive and canonicalised to upper case, spec §2) and as its
    lowercase."""
    if name is None:
        return []
    name = name.upper()
    return [name] if name.lower() == name else [name, name.lower()]


def joined_row(left, right, lnames, rnames, null_right):
    """One joined row (spec §7.4). LEFT, RIGHT: the elements as bound (already
    extended); RIGHT is None for an unmatched LINK_LEFT row."""
    row = []
    pos = {}

    def put(k, v):
        if k not in pos:
            pos[k] = len(row)
            row.append((k, v))

    def bind(k, v):
        if k in pos:
            row[pos[k]] = (k, v)
        else:
            put(k, v)

    for k, v in left[1]:
        if nested(v):
            put(k, v)
    for n in lnames + ['_1']:
        bind(n, left)
    rside = right if right is not None else null_right
    for n in rnames + ['_2']:
        bind(n, rside)
    right_names = {k.upper() for k, _ in rside[1]} if rside[0] == 'rec' else set()
    left_names = {k.upper() for k, _ in left[1]}
    for k, v in left[1]:
        if not nested(v) and k.upper() not in right_names:
            put(k, v)
    if right is not None:
        for k, v in right[1]:
            if not nested(v) and v != NULL and k.upper() not in left_names:
                put(k, v)
    return ('rec', row)


class MissingKey(Exception):
    """The key expression reads a field the element lacks: E_NO_KEY as
    written. The generator discards such programs -- its subject is rows."""


def key_of(v):
    """An equi-join key on `==`: a number matches the same number; NULL
    matches nothing. The generator only makes integer keys and NULLs."""
    if v is None:
        raise MissingKey()
    if v == NULL:
        return None
    return int(v[1])


def link(left_elems, right_elems, lnames, rnames, lkey, rkey, left_join):
    """LKEY/RKEY read the key from a bound element."""
    lb = [extend(e, lnames) for e in left_elems]
    rb = [extend(e, rnames) for e in right_elems]
    if left_join:
        # Shaped like the right elements as bound: the first one's keys; with
        # no right elements, the keys binding would add (the name and its
        # lowercase), and with none of those either, no record at all.
        shape = [k for k, _ in rb[0][1]] if rb else list(dict.fromkeys(rnames))
        null_right = ('rec', [(k, NULL) for k in shape]) if shape else NULL
    else:
        null_right = None
    buckets = {}
    for r in rb:
        k = key_of(rkey(r))
        if k is not None:
            buckets.setdefault(k, []).append(r)
    out = []
    for l in lb:
        k = key_of(lkey(l))
        matches = buckets.get(k, []) if k is not None else []
        if matches:
            out.extend(joined_row(l, r, lnames, rnames, None) for r in matches)
        elif left_join:
            out.append(joined_row(l, None, lnames, rnames, null_right))
    return out
