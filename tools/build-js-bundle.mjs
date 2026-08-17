#!/usr/bin/env node
// Bundles the JS implementation into a single file, so it can be used from a
// browser or a CDN without a build step of its own:
//
//     import { evaluate } from 'https://unpkg.com/sel-lang/dist/sel.mjs';
//
// The bundle is NOT committed. It is built by `npm run build`, rebuilt by
// `prepublishOnly` so the published tarball always carries one made from the
// tagged source, and `dist/` is gitignored. A generated file in version control
// is a file that goes stale, and a stale bundle would be a fifth implementation
// disagreeing with the other four while sitting outside the test bench.
//
// It does not sit outside the bench here: tools/impls.sh registers the bundle as
// `js-bundle`, so tools/check.sh runs the conformance suite, the documentation
// examples and the differential fuzzer against this file too. That is what makes
// bundling safe rather than hopeful — in particular it is what would catch a
// tree-shake of js/src/builtins/*.mjs, whose only job is to register themselves
// in the function table. Shake those away and you get a language with no
// functions in it, and every call becomes E_UNKNOWN_FUNC.

import { build } from 'esbuild';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
mkdirSync(resolve(ROOT, 'dist'), { recursive: true });

const common = {
  entryPoints: [resolve(ROOT, 'js/src/sel.mjs')],
  bundle: true,
  format: 'esm',
  platform: 'neutral',   // no Node built-ins: js/src/ uses none, and must not
  target: ['es2022'],
  // The built-ins register by side effect. Nothing may be dropped.
  treeShaking: false,
  legalComments: 'inline',
  banner: {
    js: '// SEL — https://github.com/nathanjel/sel — MIT. Generated; do not edit.',
  },
};

await build({ ...common, outfile: resolve(ROOT, 'dist/sel.mjs') });
await build({ ...common, outfile: resolve(ROOT, 'dist/sel.min.mjs'), minify: true });

const { statSync } = await import('node:fs');
for (const f of ['dist/sel.mjs', 'dist/sel.min.mjs']) {
  console.log(`${f}  ${(statSync(resolve(ROOT, f)).size / 1024).toFixed(1)} kB`);
}
