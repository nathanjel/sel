// Rendering C++ from the dialect map's own vocabulary.
//
// Shared by tools/gen-sql-map.mjs and tools/gen-sql-cases.mjs, because both
// turn the same shapes into the same typed constructor calls: an entry into an
// EntrySpec, a dialect declaration into a DialectSpec. Two copies would be two
// things to keep in step, and the one that drifted would drift silently -- the
// generated file would still compile.
//
// A shape the typed constructors cannot express throws Unrepresentable. That is
// not an error in itself: sql/cases carries cases whose whole point is a
// malformed binding, and C++ refuses those at compile time instead of at run
// time. The CALLER decides what that means.

export class Unrepresentable extends Error {
  constructor(why) { super(why); this.why = why; }
}

export const shapeOf = (v) => Array.isArray(v) ? 'a list'
  : v === null ? 'null'
  : typeof v === 'object' ? 'a map'
  : typeof v;

export function cppStr(s) {
  const bytes = Buffer.from(String(s), 'utf8');
  let out = '"';
  for (const b of bytes) {
    if (b === 0x5c) out += '\\\\';
    else if (b === 0x22) out += '\\"';
    // Three-digit octal cannot run on into the character beside it, as \x can.
    // EVERY byte outside printable ASCII takes this form, not just the control
    // ones: `String.fromCharCode(b)` on a byte >= 0x80 makes a JS char in
    // latin1, and writeFileSync then re-encodes THAT as UTF-8, so `ż` (c5 bc)
    // was written as c3 85 c2 bc. The generated case data carried the
    // double-encoded string, and the cases still passed, because the source and
    // the expectation were corrupted identically -- so C++ was quietly running
    // `zaÅ¼Ã³ÅÄ` where the other three hosts ran `zażółć`. Octal keeps the file
    // pure ASCII and byte-exact, and no encoding step can touch it.
    else if (b < 0x20 || b >= 0x7f) out += '\\' + b.toString(8).padStart(3, '0');
    else out += String.fromCharCode(b);
  }
  out += '"';
  // A NUL does not survive a `const char*`: the std::string built from one
  // stops there, so a column named "a\0b" becomes "a" and the check that no
  // dialect can quote a NUL never fires. The length form keeps the bytes.
  return bytes.includes(0) ? `std::string(${out}, ${bytes.length})` : out;
}

export function cppName(v, what) {
  if (typeof v !== 'string') throw new Unrepresentable(`${what} that is ${shapeOf(v)}`);
  return cppStr(v);
}

export const SECTIONS = { ops: 'Ops', funcs: 'Funcs', skel: 'Skel' };

const ENTRY_FIELDS = ['tpl', 'variants', 'ret', 'caveat', 'since', 'arity', 'builder'];

export function cppEntrySpec(entry, section) {
  if (entry === null) return 'EntrySpec::withdraw()';
  if (typeof entry === 'string') return `EntrySpec::withdraw(${cppStr(entry)})`;
  if (typeof entry !== 'object' || Array.isArray(entry)) {
    throw new Unrepresentable(`a map entry that is ${shapeOf(entry)}`);
  }
  if (entry.builder !== undefined) {
    throw new Unrepresentable('a builder entry, which no document declares');
  }
  // A null arm WITHDRAWS that count -- sql/MAP.md §2 -- so it is std::nullopt
  // rather than unrepresentable.
  const arms = (o) => Object.entries(o).map(([k, t]) => {
    if (t === null) return `{${cppStr(k)}, std::nullopt}`;
    if (typeof t !== 'string') throw new Unrepresentable(`a template arm that is ${shapeOf(t)}`);
    return `{${cppStr(k)}, ${cppStr(t)}}`;
  }).join(', ');

  const hasRet = entry.ret !== undefined && entry.ret !== null;
  let base;
  if (entry.variants !== undefined) {
    if (typeof entry.variants !== 'object' || Array.isArray(entry.variants)) {
      throw new Unrepresentable(`variants that are ${shapeOf(entry.variants)}`);
    }
    if (!hasRet) throw new Unrepresentable('variants with no ret');
    base = `EntrySpec::variants({${arms(entry.variants)}}, ${cppStr(entry.ret)})`;
  } else if (typeof entry.tpl === 'string') {
    // A template with no ret is spelled skeleton() -- right for a skel entry
    // and, for ops or funcs, exactly the registration the runtime refuses for
    // having no kind. Same case, same refusal, one line later.
    base = hasRet ? `EntrySpec::tpl(${cppStr(entry.tpl)}, ${cppStr(entry.ret)})`
                  : `EntrySpec::skeleton(${cppStr(entry.tpl)})`;
  } else if (entry.tpl !== null && typeof entry.tpl === 'object' && !Array.isArray(entry.tpl)) {
    if (!hasRet) throw new Unrepresentable('an arity-keyed template with no ret');
    base = `EntrySpec::by_count({${arms(entry.tpl)}}, ${cppStr(entry.ret)})`;
  } else {
    throw new Unrepresentable(`a tpl that is ${shapeOf(entry.tpl)}`);
  }

  let out = base;
  if (entry.caveat !== undefined && entry.caveat !== null) {
    out += `.caveat(${cppName(entry.caveat, 'a caveat')})`;
  }
  if (entry.since !== undefined && entry.since !== null) {
    out += `.since(${cppName(entry.since, 'a since')})`;
  }
  if (entry.arity !== undefined && entry.arity !== null) {
    const a = entry.arity;
    if (!Array.isArray(a) || a.length !== 2 || !a.every((x) => Number.isInteger(x))) {
      throw new Unrepresentable(`an arity that is ${shapeOf(a)} of non-integers`);
    }
    out += `.arity(${a[0]}, ${a[1]})`;
  }
  for (const k of Object.keys(entry)) {
    if (!ENTRY_FIELDS.includes(k)) throw new Unrepresentable(`the entry field ${JSON.stringify(k)}`);
  }
  return out;
}

// `allow` names keys a caller tolerates and ignores — the authored documents
// carry `notes`, which every host's replay skips.
export function cppDialectSpec(doc, allow = []) {
  const known = ['dialect', 'extends', 'version', 'target', 'lexical', ...allow];
  for (const k of Object.keys(doc)) {
    // ops/funcs/skel in a dialect DECLARATION is a registration that looks like
    // it worked; the dynamic hosts refuse it by name, and DialectSpec has
    // nowhere to put it.
    if (!known.includes(k)) throw new Unrepresentable(`a dialect declaration carrying ${JSON.stringify(k)}`);
  }
  if (!('extends' in doc)) throw new Unrepresentable('a dialect declaration with no extends');

  let out;
  if (doc.extends === null) {
    if (typeof doc.version !== 'string') {
      throw new Unrepresentable('a root dialect with no version');
    }
    out = `DialectSpec::root(${cppStr(doc.version)})`;
  } else {
    out = `DialectSpec::extending(${cppName(doc.extends, 'an extends')})`;
    if (doc.version !== undefined && doc.version !== null) {
      out += `.version(${cppName(doc.version, 'a version')})`;
    }
  }
  if (doc.target !== undefined && doc.target !== null) {
    if (typeof doc.target !== 'boolean') throw new Unrepresentable(`a target that is ${shapeOf(doc.target)}`);
    out += `.target(${doc.target})`;
  }
  for (const [k, v] of Object.entries(doc.lexical ?? {})) {
    if (v === null) out += `.lexical(${cppStr(k)}, std::nullopt)`;
    else if (typeof v === 'string') out += `.lexical(${cppStr(k)}, ${cppStr(v)})`;
    else if (typeof v === 'object' && !Array.isArray(v)) {
      const e = Object.entries(v).map(([a, b]) => {
        if (typeof b !== 'string') throw new Unrepresentable(`an escape that is ${shapeOf(b)}`);
        return `{${cppStr(a)}, ${cppStr(b)}}`;
      }).join(', ');
      out += `.lexical_escapes(${cppStr(k)}, {${e}})`;
    } else throw new Unrepresentable(`a lexical value that is ${shapeOf(v)}`);
  }
  return out;
}
