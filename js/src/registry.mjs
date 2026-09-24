// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

import { BUILTIN_MANIFEST, BINDING_FORMS } from './_builtin_manifest.mjs';
import { RESERVED, asciiUpper } from './lexer.mjs';
import { Value } from './value.mjs';

// A host's own binding function (register(..., { binds: true }), examples/
// fn-complex) has no manifest forms; it gets the two classic shapes.
const GENERIC_FORMS = Object.freeze([
  { scopes: ['outer', 'inner'], when: null, binds: ['_', '_K'] },
  { scopes: ['outer', 'binder', 'inner'], when: { arg: 1, is: 'name' }, binds: ['_K'] },
]);

// Which argument of a binding call runs where (spec/builtins.md, "Binding
// forms"): for each argument 'outer' (evaluated where the call is), 'binder' (a
// bare name, never evaluated) or 'inner' (once per element, with `binds` and
// every binder's name in scope). Null when the call is not a binding builtin
// or no form takes this count — the evaluator would refuse it, and a static
// consumer treats every argument as outer. The dependency walker and the SQL
// layer's stage 1 both classify through here, so they cannot disagree.
export function bindingForm(name, args, spec = lookup(name)) {
  const forms = BINDING_FORMS[name.toUpperCase()] || (spec && spec.binds ? GENERIC_FORMS : null);
  if (!forms) return null;
  for (const form of forms) {
    if (form.scopes.length !== args.length) continue;
    if (form.when) {
      const a = args[form.when.arg];
      const ok = form.when.is === 'name' ? (a.t === 'var' && !a.grouped) : a.t === 'text';
      if (!ok) continue;
    }
    const binds = [...form.binds];
    form.scopes.forEach((scope, i) => {
      if (scope === 'binder' && args[i].t === 'var') binds.push(args[i].name);
    });
    return { scopes: form.scopes, binds };
  }
  return null;
}

const table = new Map();

// The shipped table is authored once, in spec/builtins.json, and rendered into
// _builtin_manifest.mjs. define() is how the shipped builtins register, so a
// name the manifest knows is held to it: min/max/lazy/binds must agree, and the
// extra arity rule (COND's odd count, LINK's three-or-five) is taken from the
// manifest rather than written here — one body for all five hosts. A name the
// manifest does not know is a host's own function (examples/fn-*) and passes.
export function define(spec) {
  const name = spec.name.toUpperCase();
  if (table.has(name)) throw new Error(`SEL function ${name} defined twice`);
  table.set(name, makeSpec(reconcile(name, spec)));
}

function reconcile(name, spec) {
  const m = BUILTIN_MANIFEST[name];
  if (!m) return spec;
  const max = spec.max === undefined ? spec.min : spec.max;
  const wrong = [];
  if (spec.min !== m.min) wrong.push(`min ${spec.min} vs ${m.min}`);
  if (max !== m.max) wrong.push(`max ${max} vs ${m.max}`);
  if (!!spec.lazy !== m.lazy) wrong.push(`lazy ${!!spec.lazy} vs ${m.lazy}`);
  if (!!spec.binds !== m.binds) wrong.push(`binds ${!!spec.binds} vs ${m.binds}`);
  if (spec.arityError) wrong.push('an arity rule of its own, which the manifest owns');
  if (wrong.length) {
    throw new Error(`SEL function ${name} disagrees with spec/builtins.json: ${wrong.join('; ')}`);
  }
  return m.arity ? { ...spec, arityError: manifestArityError(m.arity) } : spec;
}

function manifestArityError(rule) {
  const message = (n) => rule.message.replace('{count}', String(n));
  if (rule.parity) {
    const odd = rule.parity === 'odd';
    return (n) => ((n % 2 === 1) === odd ? null : message(n));
  }
  const allowed = new Set(rule.allowed);
  return (n) => (allowed.has(n) ? null : message(n));
}

// Called once the shipped modules have registered: a manifest entry with no
// definition is a host that would silently lack a builtin the others have.
export function assertManifestCovered() {
  const missing = Object.keys(BUILTIN_MANIFEST).filter((name) => !table.has(name));
  if (missing.length) {
    throw new Error(`spec/builtins.json names builtins this host never defined: ${missing.join(', ')}`);
  }
}

export function register(nameOrSpec, min, max, fn, options = {}) {
  const spec = typeof nameOrSpec === 'string'
    ? { ...options, name: nameOrSpec, min, max, fn }
    : nameOrSpec;
  const name = spec.name.toUpperCase();
  if (spec.overwrite === false && table.has(name)) {
    throw new Error(`SEL function ${name} defined twice`);
  }
  table.set(name, makeSpec(spec));
  return table.get(name);
}

export const registerBuiltin = register;

// Names registered through registerFunction(), which alone may be replaced.
const hostNames = new Set();

// An application's own strict function (spec/SPEC.md §8.1). It adds to the
// language and never changes it: a builtin's name or a reserved word is
// refused, and re-registering a host function replaces it. A bad registration
// is a programming error, so it throws TypeError/RangeError, not SelError.
export function registerFunction(name, min, max, fn) {
  if (typeof name !== 'string' || !/^[A-Za-z][A-Za-z0-9_]*$/.test(name)) {
    throw new TypeError(`SEL function name must be ASCII letters, digits and _, starting with a letter: ${String(name)}`);
  }
  const key = name.toUpperCase();
  if (RESERVED.has(key)) throw new RangeError(`${key} is a reserved word`);
  if (table.has(key) && !hostNames.has(key)) {
    throw new RangeError(`${key} is a builtin; a host function cannot replace it`);
  }
  if (!Number.isInteger(min) || !Number.isInteger(max) || min < 0 || max < min) {
    throw new RangeError(`SEL function ${key}: arity must be whole numbers with 0 <= min <= max`);
  }
  if (typeof fn !== 'function') throw new TypeError(`SEL function ${key}: fn is not callable`);
  table.set(key, makeSpec({
    name: key, min, max,
    fn: (args) => {
      const result = fn(args);
      if (!(result instanceof Value)) {
        throw new TypeError(`SEL function ${key} returned ${typeof result}, not a Value`);
      }
      return result;
    },
  }));
  hostNames.add(key);
}

// The [min, max] of a host function registered with registerFunction(), or
// null when the name is not one. The SQL layer reads it: a host function's SQL
// spelling is checked against, and recorded with, this arity.
export function hostArity(name) {
  const key = asciiUpper(String(name));
  if (!hostNames.has(key)) return null;
  const spec = table.get(key);
  return [spec.min, spec.max];
}

function makeSpec(spec) {
  const name = spec.name.toUpperCase();
  return {
    name,
    min: spec.min,
    max: spec.max === undefined ? spec.min : spec.max,  // Infinity for variadic
    lazy: !!spec.lazy,
    binds: !!spec.binds,   // introduces an element binder; see dependencies()
    // Optional extra arity rule, checked at compile time after min/max. Returns
    // a message when the count is wrong, or null when it is fine.
    arityError: spec.arityError || null,
    fn: spec.fn,
  };
}

export function lookup(name) { return table.get(name.toUpperCase()); }
export function names() { return Array.from(table.keys()).sort(); }
