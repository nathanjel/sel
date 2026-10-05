// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

import { BUILTIN_MANIFEST, BINDING_FORMS } from './_builtin_manifest.mjs';
import { RESERVED, asciiUpper } from './lexer.mjs';
import { Value } from './value.mjs';

// A binding function the manifest does not know (one defined in place, as
// examples/fn-complex does with define()) has no manifest forms; it gets the
// two classic shapes.
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
  const form = matchForm(name, args, spec);
  if (!form) return null;
  const binds = [...form.binds];
  form.scopes.forEach((scope, i) => {
    if (scope === 'binder' && args[i].t === 'var') binds.push(args[i].name);
  });
  return { scopes: form.scopes, binds };
}

// The manifest form a call's argument nodes take: the first whose count
// matches and whose `when` holds. The order is the manifest's, which is what
// makes a text literal in SORT_BY's third slot a direction even when the
// second slot is a bare name (spec/builtins.md, "Binding forms").
// `name` is a call node's, which the lexer has already upper-cased.
function matchForm(name, args, spec) {
  const forms = Object.hasOwn(BINDING_FORMS, name) ? BINDING_FORMS[name]
    : (spec && spec.binds ? GENERIC_FORMS : null);
  if (!forms) return null;
  for (const form of forms) {
    if (form.scopes.length !== args.length) continue;
    if (form.when) {
      const a = args[form.when.arg];
      const ok = form.when.is === 'name' ? (a.t === 'var' && !a.grouped) : a.t === 'text';
      if (!ok) continue;
    }
    return form;
  }
  return null;
}

// Where a binding call's arguments sit, read off the form matchForm picks --
// the one decoder the evaluator, the optimiser, the planner and the translator
// share, so "which slot is the key, which the direction" is answered once:
//   binder  the index of the bare-name binder, or -1 for the implicit `_`
//           (the slot may still hold a non-name: the evaluator then raises
//           E_EXPECT_SYMBOL, and a static reader treats the call as opaque);
//   body    the first argument evaluated per element -- MAP's projection,
//           FILTER's predicate, a sort's key, BUCKET's key -- or -1 (SORT(L),
//           TOP(L, n): the elements are their own keys);
//   extra   a second per-element argument (BUCKET's projection), or -1;
//   after   the arguments after the body evaluated where the call stands, in
//           order: a sort's direction, then a TOP's count.
// Null where bindingForm is null. The answer for a form is built once.
export function argRoles(name, args, spec = lookup(name)) {
  const form = matchForm(name, args, spec);
  return form === null ? null : rolesOf(form);
}

// Whether a text literal in slot `index` is what selects one of NAME's forms at
// this argument count (SORT_BY's and TOP_BY's direction): folding a constant
// into a text literal there would change the form the call takes.
export function textSelectsForm(name, count, index) {
  const forms = Object.hasOwn(BINDING_FORMS, name) ? BINDING_FORMS[name] : null;
  return forms !== null && forms.some((f) => f.scopes.length === count && f.when !== null
    && f.when.is === 'text' && f.when.arg === index);
}

// argRoles for the evaluator, which asks once per call it evaluates: cached by
// the call's argument array (a parse tree is immutable once built, and the
// optimised tree run() evaluates is built once). Manifest forms only: a host's
// own binding function decodes its arguments itself.
const ROLES_BY_ARGS = new WeakMap();
export function callRoles(name, args) {
  let roles = ROLES_BY_ARGS.get(args);
  if (roles === undefined) {
    roles = argRoles(name, args, null);
    ROLES_BY_ARGS.set(args, roles);
  }
  return roles;
}

const ROLES = new WeakMap();
function rolesOf(form) {
  let roles = ROLES.get(form);
  if (roles === undefined) {
    const s = form.scopes;
    const body = s.indexOf('inner');
    const after = [];
    for (let i = (body < 0 ? 0 : body) + 1; i < s.length; i++) if (s[i] === 'outer') after.push(i);
    roles = Object.freeze({
      binder: s.indexOf('binder'),
      body,
      extra: body < 0 ? -1 : s.indexOf('inner', body + 1),
      after: Object.freeze(after),
    });
    ROLES.set(form, roles);
  }
  return roles;
}

const table = new Map();

// Names a host registered (register / registerFunction): the only ones that may
// be replaced.
const hostNames = new Set();

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

// DEPRECATED public spelling, kept for one release: use registerFunction().
// It used to register anything -- a lazy or binding function, any name, an
// arity with min > max, an fn whose native return value leaked out of
// evaluate() -- which SPEC §8.1 does not allow a host function. It now takes
// the same strict, validated path: `{ lazy, binds, arityError, compileCheck }`
// are refused, a missing max means max = min, and `overwrite: false` still
// refuses a name already registered.
export function register(nameOrSpec, min, max, fn, options = {}) {
  const spec = typeof nameOrSpec === 'string'
    ? { ...options, name: nameOrSpec, min, max, fn }
    : nameOrSpec;
  if (spec === null || typeof spec !== 'object') {
    throw new TypeError('register takes (name, min, max, fn) or a { name, min, max, fn } spec');
  }
  for (const key of ['lazy', 'binds', 'arityError', 'compileCheck']) {
    if (spec[key]) {
      throw new TypeError(`SEL function ${String(spec.name)}: a host function is strict (spec §8.1); `
        + `'${key}' is not supported`);
    }
  }
  const key = typeof spec.name === 'string' ? spec.name.toUpperCase() : spec.name;
  if (spec.overwrite === false && hostNames.has(key)) {
    throw new Error(`SEL function ${key} defined twice`);
  }
  registerFunction(spec.name, spec.min, spec.max === undefined ? spec.min : spec.max, spec.fn);
  return table.get(key);
}

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
    // Optional compile-time check of the call's argument NODES (a literal regex
    // pattern is validated when the program compiles, not when it runs).
    compileCheck: spec.compileCheck || null,
    fn: spec.fn,
  };
}

export function lookup(name) { return table.get(name.toUpperCase()); }
// Whether `name` is a function a host registered (and so could do anything,
// including write into the values it is handed).
export function isHostFunction(name) { return hostNames.has(String(name).toUpperCase()); }
export function names() { return Array.from(table.keys()).sort(); }
