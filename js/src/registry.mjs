// The function table. Fixed at startup — SEL has no DEFUN — which is what lets
// unknown names and wrong argument counts be caught at compile time.

const table = new Map();

export function define(spec) {
  const name = spec.name.toUpperCase();
  if (table.has(name)) throw new Error(`SEL function ${name} defined twice`);
  table.set(name, makeSpec(spec));
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
