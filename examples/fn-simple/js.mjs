// Goes in the relevant js/src/builtins/*.mjs. Not a runnable file: `define` is
// internal to js/src/registry.mjs and this fragment compiles only in place.
// EXAMPLE-BEGIN
define({
  name: 'ORD_SUFFIX', min: 1, max: 1,
  fn: (args) => {
    const n = args.nonNegInt(0);
    const tens = n % 100;
    if (tens >= 11 && tens <= 13) return Value.text(`${n}th`);
    const ones = n % 10;
    return Value.text(`${n}${ones === 1 ? 'st' : ones === 2 ? 'nd' : ones === 3 ? 'rd' : 'th'}`);
  },
});
// EXAMPLE-END
