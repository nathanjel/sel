#!/usr/bin/env node
// Extracts the worked examples from the documentation into a corpus every
// implementation can run.
//
// Any line inside a ```sel fenced block of the form
//
//     EXPRESSION  =>  RESULT
//
// is a doc-test. RESULT is written the way bin/sel prints it: bare text, TRUE or
// FALSE, bin:<hex>, a tree dump, or !E_CODE for an error — which is exactly what
// `batch --show` emits. So the check is: run the corpus, diff against the
// expectations. Documentation that cannot be checked is documentation that
// drifts.
//
// This scanner is deliberately the only one in the project. Before, each host
// re-implemented it; now every implementation reuses the batch runner it already
// needs for the fuzzer, and gets doc-checking for free.
//
//   node tools/extract-docs.mjs out-prefix README.md docs/LANGUAGE.md ...
//
// writes four index-aligned files, one line per example in the last three:
//   <out-prefix>.selc   the corpus
//   <out-prefix>.want   the expectations
//   <out-prefix>.src    the source, for failure reports
//   <out-prefix>.where  file:line, for failure reports
//
// `.src` exists so the reporting side never has to compute a line number inside
// the corpus. Doc examples are single-line today, but the corpus format permits
// multi-line records, and arithmetic that assumes otherwise would quote the
// wrong line the day that changes.

import { readFileSync, writeFileSync } from 'node:fs';

const SEP = '  => ';

export function extract(text, file) {
  const tests = [];
  let inBlock = false;
  text.split('\n').forEach((line, i) => {
    if (line.trimStart().startsWith('```')) {
      const info = line.trim().slice(3).trim();
      inBlock = !inBlock && info === 'sel';
      return;
    }
    if (!inBlock) return;
    if (line.trimStart().startsWith('#')) return;      // a SEL comment, not a test
    const at = line.lastIndexOf(SEP);
    if (at < 0) return;
    const source = line.slice(0, at).trim();
    const want = line.slice(at + SEP.length).trim();
    // `want` may legitimately be empty — an example whose result is empty text
    // is still an example, and dropping it would remove it from the check
    // silently. Only a missing source disqualifies a line.
    if (source) tests.push({ file, line: i + 1, source, want });
  });
  return tests;
}

const [prefix, ...files] = process.argv.slice(2);
if (!prefix || files.length === 0) {
  process.stderr.write('usage: extract-docs.mjs out-prefix file.md...\n');
  process.exit(2);
}
const tests = files.flatMap((f) => extract(readFileSync(f, 'utf8'), f));

// Nothing to compare is not success. A broken fence marker or a mistyped file
// list would otherwise leave every implementation passing a zero-length check.
if (tests.length === 0) {
  process.stderr.write(`no doc examples found in: ${files.join(' ')}\n`);
  process.exit(1);
}

writeFileSync(`${prefix}.selc`, tests.map((t, i) => `### ${i + 1}\n${t.source}\n`).join(''));
writeFileSync(`${prefix}.want`, tests.map((t) => t.want).join('\n') + '\n');
writeFileSync(`${prefix}.src`, tests.map((t) => t.source).join('\n') + '\n');
writeFileSync(`${prefix}.where`, tests.map((t) => `${t.file}:${t.line}`).join('\n') + '\n');

process.stderr.write(`${tests.length} doc examples extracted from ${files.length} files\n`);
