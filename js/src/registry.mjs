// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

import { BUILTIN_MANIFEST } from './_builtin_manifest.mjs';

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
