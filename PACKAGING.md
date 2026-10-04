# Publishing

The package is called **`sel-lang`** on every registry. `sel` was already taken
in most of them: an npm CSS-selector library, a `projects/sel` entry in
quicklisp-projects (GrammaTech's Software Evolution Library), and a `sel` project
on PyPI.

| Registry | Name | Manifest |
|---|---|---|
| PyPI | `sel-lang` | `pyproject.toml` |
| npm | `sel-lang` | `package.json` |
| Packagist | `nathanjel/sel-lang` | `composer.json` |
| Quicklisp / Ultralisp | `sel-lang` | `lisp/sel-lang.asd` |
| Conan | `sel-lang` | `cpp/conanfile.py` |
| vcpkg | `sel-lang` | `cpp/vcpkg.json` |
| crates.io | `sel-lang` (the library is `sel_lang`) | `rust/Cargo.toml` |
| Go module proxy | `github.com/nathanjel/sel/go` | `go/go.mod`, versioned by `go/vX.Y.Z` tags |

Packagist requires a vendor prefix, so `nathanjel/` is unavoidable there. The
repository itself is `nathanjel/sel`; only the published package is `sel-lang`.

Nothing about publishing changes how the project is used without a package
manager: copying a directory still works, and that remains the primary story in
the README.

---

## Before any release

```
tools/check-generated.sh                          # artifacts freshly generated
tools/check.sh                                    # ALL GREEN, full roster
SEL_IMPLS="$SEL_IMPLS python-wheel" tools/check.sh # and through the built wheel
tools/oracle-db.sh                                # the map, against real servers
tools/oracle-db.sh run python3 tools/mutate-sql.py # every mutation, none skipped
tools/check-version.sh 0.9.1                      # every manifest agrees
tools/check-package-docs.sh                       # user docs only, in every package
tools/check-cpp-package.sh                        # the C++ package builds as a consumer gets it
tools/check-rust-package.sh --publish-dry-run     # the crate as crates.io gets it, and cargo's dry run
tools/check-go-module.sh                          # the Go module as the proxy zips it
tools/check-usage.sh                              # the SQL examples, every host, real servers
```

**Only the user documentation ships.** The pages a reader of the docs is sent
to — `docs/README.md`, the language pages (`overview`, `parity`, `syntax`,
`operators`, `functions`), `sql.md`, `extending.md`, everything in
`docs/usage/`, and the generated `docs/reference/` — go out with every package;
the contributor guide (`docs/contributing.md`), the design documents in
`docs/internals/`, the site's assets and build files, this file and `CLAUDE.md`
do not. The Rust crate and the Go module carry no `docs/` at all — each is
only its own directory — so their READMEs link to the documentation. Three configurations carry that list — `files` in `package.json`, the sdist
`include` in `pyproject.toml`, and `.gitattributes`' `export-ignore`, which is
what `git archive` and so Packagist's dist and GitHub's tag tarballs honour — and
each lists the user documents file by file, so a new note in `docs/` stays out
until someone lets it in. `tools/check-package-docs.sh` (a gate layer) holds all
three to one list; a new user document is added to all four places. Quicklisp and
Ultralisp clone the repository, so they alone carry everything.

The first command above is what makes the rest of this document possible. The SEL→SQL map is
authored once, in `sql/dialects/*.json`, and rendered by `tools/gen-sql-map.mjs`
into each host's own source language — `MapData.php`, `_map.py`, `_map.mjs`,
`sel_sql_map_data.cpp`. Those renderings are committed and published, so a C++,
PHP or Python user never installs Node to get a working library; the price is
that a release cut from a tree where one of them is stale ships hosts that
quietly disagree about the map. `check-generated.sh` refuses that, names the
artifact, and prints the command to fix it. Unlike `check-sql-map.sh` it does
not skip when Node is absent — it falls back to timestamps, because a release
machine without Node is precisely where the mistake would otherwise pass.

`tools/check.sh` must print `ALL GREEN` with every implementation present — a partial
roster is refused rather than quietly passing, because every differential layer
degrades to a no-op when there is nothing to compare against.

Then tag. Every registry below either reads the tag or is told the version by
hand, and they must agree:

```
git tag -a v0.9.1 -m "SEL 0.9.1"
git tag go/v0.9.1 v0.9.1                  # the Go module's version: the module is go/
git push origin v0.9.1 go/v0.9.1
tools/check-version.sh 0.9.1 --tags       # both tags exist, on one commit
```

**Never re-tag or move an existing tag.** Upstream registries forbid republishing under an existing version: Packagist blocks re-tagged releases with `Upstream re-tag blocked — Packagist may no longer match the VCS repo for this version`, while npm and PyPI permanently refuse file uploads for already-published versions. If a defect or correction is needed after pushing a tag, always bump to the next patch version.

Versions live in seven manifests. Keep them in step:

```
package.json                     "version": "0.9.1"
pyproject.toml                   version = "0.9.1"
cpp/conanfile.py                 version = "0.9.1"
cpp/vcpkg.json                   "version-semver": "0.9.1"
cpp/CMakeLists.txt               project(... VERSION 0.9.1 ...)
lisp/sel-lang.asd                :version "0.9.1"
rust/Cargo.toml                  version = "0.9.1"
```

`go/go.mod` has no version: a Go module's version is its tag, `go/v0.9.1`.

`python/sel/__init__.py` carries `__version__`, `CHANGELOG.md`'s top heading
carries the version being released, and `composer.json` carries
`extra.branch-alias.dev-main` (`0.9.x-dev`). All are checked against the release
version by `tools/check-version.sh` (which also holds the pinned `sel-lang@X` CDN
URLs and `sel-lang/X` Conan references in `README.md` and
`docs/usage/README.md` to it), so they are places fewer to remember rather
than more — and a release whose notes or branch alias were never updated fails
the check before the tag is cut.

`composer.json` deliberately carries **no** `version` field — Packagist infers
release versions from git tags, and hard-coding it there is a known way to
publish a lie.

## GitHub release and CDN

After the tag and the registries (PyPI, npm, crates.io, the Go proxy;
Packagist follows the tag by itself), the release page gets one file per host,
built from a checkout of the tag:

```
tools/release-assets.sh 0.9.1              # dist/release-0.9.1/ and its NOTES.md, to look over
tools/release-assets.sh 0.9.1 --publish    # the same, then gh release create v0.9.1 with them
```

The script needs the pushed tags (`v0.9.1` and `go/v0.9.1`), the npm tarball in `dist/npm/`, the browser
bundles in `dist/` and the wheel and sdist in `dist/python/`. It refuses when
`js/src`, `python/sel` or a manifest differs from the tag, and when the
CHANGELOG has no entry for the version, and with `--publish` it refuses unless
`tools/check-release-registries.sh` confirms that crates.io and the Go proxy
serve this release, with the same bytes and from the same commit (without
`--publish`, a failed check is a warning). What it makes:

| File | For |
|---|---|
| `sel-lang-0.9.1-js-npm.tar.gz` | the npm package, renamed to say so; `npm install ./…tar.gz` installs it |
| `sel-lang-0.9.1-js-bundle.mjs`, `…js-bundle.min.mjs` | the standalone browser modules, `dist/sel.mjs` and `dist/sel.min.mjs` |
| `sel_lang-0.9.1-py3-none-any.whl`, `sel_lang-0.9.1.tar.gz` | the wheel and sdist, under their standard names: pip reads the version and tags from the file name |
| `sel-lang-0.9.1-php-source.tar.gz` | `php/src` and `composer.json` |
| `sel-lang-0.9.1-cpp-source.tar.gz` | the library sources, `third_party/srell`, `test_package` and the CMake, Conan and vcpkg manifests |
| `sel-lang-0.9.1-lisp-source.tar.gz` | `lisp/sel-lang.asd` and `lisp/src` |
| `sel-lang-0.9.1.crate` | the Rust crate exactly as crates.io gets it (`cargo package`, from a `rust/` that matches the tag) |
| `sel-lang-0.9.1-go-source.tar.gz` | the Go module directory, `go/` |

The source tarballs are `git archive` of the tag, so they carry exactly what
the tag does, `LICENSE`, `README.md` and `CHANGELOG.md` included. Every archive
is a `.tar.gz`: one archive format keeps the page readable. Each file is uploaded
with a label naming its host and what to do with it, and the notes open with a
"Which file do I want?" table (each registry's command, then the file here)
followed by the version's CHANGELOG entry. The table's minimum versions are
read from `composer.json`, `pyproject.toml` and `package.json`.

**The CDN needs nothing.** jsDelivr and unpkg mirror every public npm package,
and the npm package carries both browser bundles (`files` in `package.json`), so
the moment `npm publish` succeeds the browser import exists:

```js
import { evaluate } from 'https://cdn.jsdelivr.net/npm/sel-lang@0.9.1/dist/sel.min.mjs';
```

A versioned URL never changes. Check it answers `200` with a JavaScript content
type, and that its bytes are `dist/sel.min.mjs`'s. The release notes, the README's
install section and [Using SEL](docs/usage/README.md#installing) all give it.

---

## PyPI

```
rm -rf dist/python/*
python3 -m build --outdir dist/python
python3 -m twine check dist/python/*
python3 -m twine upload dist/python/*
```

**Build into `dist/python`, not `dist`.** The repository root's `dist/` already
holds the JavaScript bundle; letting `build` write beside it would mix two
languages' artefacts in one directory and eventually upload the wrong thing.

The wheel ships `python/sel/` and nothing else — the package, its `py.typed`
marker and the licence, about 50 kB. The sdist adds the user documents,
`spec/`, `conformance/` and the Python examples, mirroring what npm's `files`
whitelists.

The package has **no runtime dependencies**, and that is a property worth
keeping: the regex subset is small enough that `re` covers it after the anchor
rewrite, and the decimal core is deliberately hand-written (see below). Python
and JavaScript are the only two hosts with neither a vendored engine nor an
external one.

Verify the built package rather than the source tree, which is what the
`python-wheel` implementation in `tools/impls.sh` is for:

```
python3 -m venv python/.venv-wheel
python/.venv-wheel/bin/pip install dist/python/*.whl
SEL_IMPLS="python-wheel" tools/check.sh
```

That runs the whole conformance suite, the API probes and the fuzzer through the
*installed* package. It is the only layer that catches a packaging mistake — a
sub-package left out of the wheel, a missing `py.typed`, an entry point that
names a module the wheel does not contain — because every other layer imports
from `python/`.

### Two things to know

**The `sel` console script collides with npm's.** Both packages install a command
called `sel`. There is no good way around it and no attempt is made to hide it:
`python -m sel` always works and is the spelling to prefer on a machine that has
both. It is the same trade as the `SEL` package name in Common Lisp — an unlikely
collision, made loud rather than silent.

**`python/sel/decimal.py` does not use the `decimal` module**, and must not start
to. `tools/decimal-oracle.py` generates this project's decimal test cases *from*
`decimal`, as an independent third opinion on cores that were all written from
one spec by one hand. A host built on `decimal` would turn `tools/check-decimal.sh`
into a comparison of the standard library with itself — still printing
"0 mismatches", while verifying nothing at all for that host.

For automated releases, PyPI's Trusted Publishing (OIDC from a CI workflow)
removes the need for a long-lived token; a manual `twine upload` with an API
token is equally fine and is what the commands above assume.

---

## npm

```
npm pack --dry-run      # inspect the file list first
npm publish --access public
```

`files` in `package.json` whitelists what ships: `js/`, the user documents,
`spec/`, the JS examples, the licence and the README. The PHP, C++ and Lisp
trees are excluded, so the tarball is ~515 kB rather than the whole repository.

The package is ESM-only (`"type": "module"`) and exposes one entry point plus the
`sel` CLI:

```js
import { compile, evaluate, Value, SelError } from 'sel-lang';
```

`dist/sel.mjs` and `dist/sel.min.mjs`, the standalone browser bundles (no SQL
layer), ship in the package as `sel-lang/bundle` and `sel-lang/bundle.min`, which
is also what the CDNs serve (see "GitHub release and CDN" above). Run
`npm run build` before `npm pack`, or they are last release's.

`sideEffects` lists `js/src/builtins/*.mjs`, because those modules register
themselves in the function table and a bundler that tree-shook them would leave
you with a language that has no functions in it.

---

## Packagist

Submit the GitHub URL once at <https://packagist.org/packages/submit>, then add
the GitHub webhook so subsequent tags publish themselves.

Autoloading is a single `files` entry pointing at `php/src/bootstrap.php`, not
PSR-4. That is deliberate: the function table must be complete before any source
is parsed, because an unknown function name is a *compile-time* error, and PSR-4
would only load a class at the moment it is first mentioned. `bootstrap.php` uses
`require_once` throughout, so loading it twice is harmless.

Verify before publishing:

```
composer validate
```

### Branch alias (`dev-main`)

Packagist infers release versions from git tags, but it reads `extra.branch-alias.dev-main` in `composer.json` to determine what `dev-main` represents. On every minor release series bump, update this alias to match:

```json
  "extra": {
    "branch-alias": {
      "dev-main": "0.9.x-dev"
    }
  }
```

`tools/check-version.sh` enforces this against the release series, so an outdated branch alias fails the version check before tagging.

### Tag immutability

Never delete, move, or re-tag an existing release tag. Packagist explicitly tracks tag commit hashes and flags moved tags with:

> `Upstream re-tag blocked — Packagist may no longer match the VCS repo for this version`

Once a tag is pushed, it must be treated as immutable. If any fix or correction is needed post-release, cut a new patch release (e.g. `0.9.2`) rather than moving `v0.9.1`.

---

## Quicklisp and Ultralisp

The ASDF system is `sel-lang`, defined in `lisp/sel-lang.asd`. ASDF requires the
file name to match the primary system name, which is why the file was renamed.

**Ultralisp** is the quicker of the two: add the repository at
<https://ultralisp.org/>, and it scans for `.asd` files itself — including in
subdirectories, so `lisp/sel-lang.asd` is found without moving anything.

**Quicklisp** needs a pull request against
<https://github.com/quicklisp/quicklisp-projects> adding `projects/sel-lang/source.txt`:

```
git https://github.com/nathanjel/sel.git
```

Releases are cut monthly, so expect a wait.

### One thing to know

The ASDF system is `sel-lang` but the Common Lisp *package* is still `SEL`, so
the API reads `sel:evaluate`. If you ever load this alongside GrammaTech's
Software Evolution Library in one image, the package names may collide. Renaming
the package would change every call site in the public API; it has not been done
because the collision is unlikely and loud rather than silent.

---

## crates.io

The crate is `rust/` — the library and the `sel` command-line tool. Everything
the repository needs besides (the test harness binaries, the worked examples,
the benchmark hosts and the database drivers they use) is `rust/dev`, a
workspace member with `publish = false`. What the crate carries is decided by
`include` in `rust/Cargo.toml`: the sources, `README.md` (the crates.io page) and
`LICENSE` (a copy of the repository's, held identical by
`tools/check-rust-package.sh`).

```
cargo login                                        # once, with a crates.io API token (the owner's)
git checkout v0.9.1                                # publish from the tagged commit, with rust/ clean
tools/check-rust-package.sh --publish-dry-run      # package, consumer, CLI, docs, doctests, dry run
cargo publish --manifest-path rust/Cargo.toml -p sel-lang
```

**Publish from the tag.** Cargo records the checked-out commit inside the
`.crate` (`.cargo_vcs_info.json`), and packaging is otherwise reproducible, so a
crate packaged at the tag is byte for byte the one crates.io serves.
`tools/release-assets.sh` builds the release page's `.crate` that way (it
refuses unless `HEAD` is the tag) and, before it publishes, has
`tools/check-release-registries.sh` prove it: crates.io has the version, not
yanked, with that SHA-256; the Go proxy serves the module from the commit
`go/v0.9.1` names, and `go mod download` verifies it against sum.golang.org.

docs.rs builds the API documentation by itself once the version is on crates.io
(the crate front page is `README.md`); `tools/check-rust-package.sh` builds it the
same way, with rustdoc warnings as errors, and runs its doctests.

### One thing to know

**A published version is permanent.** It can be yanked — new projects will not
pick it — but never replaced or deleted; a fix is the next patch version.

## Go modules

The module is the `go/` directory, `github.com/nathanjel/sel/go`, with two
packages: `…/go/sel`, the language, and `…/go/sel/sql`, the SQL layer. There is
no registry to upload to: a version is a git tag. Because the module is not at
the repository root, its tags carry the directory as a prefix — `go/v0.9.1` —
and they go on the same commit as `v0.9.1` (`tools/check-version.sh --tags`
checks both; `tools/release-assets.sh` refuses without them).

```
git push origin v0.9.1 go/v0.9.1
GOPROXY=https://proxy.golang.org go list -m github.com/nathanjel/sel/go@v0.9.1   # the proxy fetches it
```

Then open https://pkg.go.dev/github.com/nathanjel/sel/go@v0.9.1 (or press
"Request" there) and the documentation appears: the package comments
(`go/sel/doc.go`, `go/sel/sql/doc.go`), the runnable `Example` functions
(`example_test.go`, which `go test` also runs) and `go/README.md`. pkg.go.dev
shows documentation only when it finds a licence in the module, so `go/LICENSE`
is a copy of the repository's; `tools/check-go-module.sh` holds it identical and
builds the module, a consumer and the `sel` command from exactly what the proxy
would zip.

### One thing to know

**The proxy caches a tag forever.** Moving or re-pushing `go/v0.9.1` changes
nothing for anyone who already fetched it, and the checksum database will
reject the new contents as a security failure. Never move a Go tag; bump.

## A note for C++ consumers upgrading to 0.3.0

`sel::Value` became a handle: copying one now aliases, and `clone()` is the deep
copy. Every signature in `sel.hpp` is unchanged, so this compiles silently — the
break is behavioural, not a build error, which is the awkward kind.

```cpp
Value b = a;            // 0.2.0: an independent deep copy
                        // 0.3.0: the same value as a
Value b = a.clone();    // an independent deep copy, both versions
```

The interpreter needed this to agree with the other four hosts (spec/SPEC.md
§3.4), and it makes copies cheap. Code that builds each value fresh and moves it
into place — the idiom `cpp/bin/e2e.cpp` already uses — needs no change at all.

## Conan

Conan Center does **not** have SRELL, so the vendored copy is what makes the
recipe build at all. See the note below.

Conan needs a profile before its first use, or it refuses with *"The default
build profile doesn't exist"* — that is Conan asking to be initialised, not a
problem with the recipe:

```
conan profile detect          # once, per machine
conan create cpp/ --build=missing
```

`conan create` also builds and runs `cpp/test_package/`, which links the
packaged library through the exported CMake target and nothing else, so a
recipe that produces an unusable package fails there rather than downstream.

The recipe does **not** gate on the consumer's `compiler.cppstd`. A stock
`conan profile detect` yields `gnu20`, and the library reaches C++23 through
`target_compile_features` in CMakeLists.txt, so refusing to build on the default
profile would only make the package unusable out of the box.

To publish, either upload to your own remote:

```
conan upload sel-lang/0.9.1 -r <remote> --confirm
```

or open a pull request against
<https://github.com/conan-io/conan-center-index> adding `recipes/sel-lang/`.
Conan Center requires the recipe to fetch sources from a release URL rather than
carry them, so a Center submission needs the recipe reworked around
`conan.tools.files.get()` pointing at the GitHub tarball for the tag.

---

## vcpkg

`cpp/vcpkg.json` is a manifest, usable immediately in overlay-port form. For the
public registry, open a pull request against
<https://github.com/microsoft/vcpkg> adding `ports/sel-lang/` with a
`portfile.cmake` that calls `vcpkg_from_github`, `vcpkg_cmake_configure`,
`vcpkg_cmake_install` and `vcpkg_cmake_config_fixup(PACKAGE_NAME sel-lang)`.

---

## SRELL: vendored, with an opt-out

The C++ implementation needs an ECMAScript-conformant regex engine, because that
is what makes it agree with the JavaScript host. It vendors SRELL, pinned to
release **2026.05** at commit `7bf06e58…`, under `cpp/third_party/srell/`.

Where each package manager stands:

| | SRELL available? | what SEL does |
|---|---|---|
| vcpkg | **yes**, `srell` at exactly `2026.05` | vendored by default; `system-srell` feature links vcpkg's |
| Conan | no such package | vendored, no alternative |
| plain CMake / copy the files | n/a | vendored |

The vendored copy is the default everywhere, on purpose. It is what keeps "copy
`sel.hpp`, `sel_ast.hpp`, the generated `sel_limits.hpp`, `sel_math_ops.hpp` and
`sel_builtin_manifest.hpp`, `sel.cpp`, `sel_optimizer.cpp` (which `sel.cpp`
includes) and `third_party/srell/`, and compile `sel.cpp`" true
— and, with `sel_sql*.{hpp,cpp}` added, the same for the SQL layer — it is the only
option for Conan, and it removes any chance of a resolver quietly selecting a
different engine version — which would not be a build difference, it would be a
*language* difference, since the regex engine decides what a rule matches.

To link an external SRELL instead:

```
cmake -S cpp -B build -DSEL_USE_SYSTEM_SRELL=ON     # needs find_package(srell)
vcpkg install sel-lang[system-srell]
```

`cpp/vcpkg.json` pins `srell` to `2026.05` in `overrides` so the feature cannot
silently drift to another release. Either way, **run `tools/check.sh`**: the
regex cases in `conformance/09-regex.selt` are what actually decide whether a
given SRELL still agrees with the other three implementations.
