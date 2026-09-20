# Structured code scan

From the repository root:

```sh
python3 tools/code-scan/scan.py
python3 tools/code-scan/registry.py
node tools/gen-sql-map.mjs --check
node tools/gen-sql-cases.mjs --check
```

`scan.py` inventories the five runtime source roots, skips explicitly generated
SQL tables for candidate detection, records source hashes, and emits lexical
single-reference candidates, Python AST unused-import candidates and normalized
8-line clone windows. Source comments/strings, dynamic names, public exports,
Lisp macros and C++ templates/overloads limit this analysis. Candidate counts
are not deletion instructions or a comparable per-language quality score.

`registry.py` loads each lane's startup registry and normalizes only its native
variadic sentinel to `-1`. It records all builtin names, arity bounds, lazy/binds
flags and whether an extra arity callback exists, plus compile-only four-argument
LINK/LINK_LEFT probes. It freshly compiles the C++ probe with g++/C++23 and loads
Lisp through the existing Quicklisp boot script. PHP, Node, Python, g++, SBCL and
the repository's Lisp dependencies are required. Temporary C++ binaries and
Lisp compile caches are isolated under `/tmp/sel-registry-scan-*`.

Results overwrite `docs/interim/code-scan/scan.json` and `registry.json`.
The manually reviewed report is `docs/interim/structured-code-scan.md`.
These tools do not mutate runtime sources or prove whole-program reachability.
