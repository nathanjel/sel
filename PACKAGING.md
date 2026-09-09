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
tools/check-version.sh 0.6.1                      # every manifest agrees
```

The first is what makes the rest of this document possible. The SEL→SQL map is
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
git tag -a v0.7.1 -m "SEL 0.7.1"
git push origin v0.7.1
```

**Never re-tag or move an existing tag.** Upstream registries forbid republishing under an existing version: Packagist blocks re-tagged releases with `Upstream re-tag blocked — Packagist may no longer match the VCS repo for this version`, while npm and PyPI permanently refuse file uploads for already-published versions. If a defect or correction is needed after pushing a tag, always bump to the next patch version.

Versions live in six manifests. Keep them in step:

```
package.json                     "version": "0.7.1"
pyproject.toml                   version = "0.7.1"
cpp/conanfile.py                 version = "0.7.1"
cpp/vcpkg.json                   "version-semver": "0.7.1"
cpp/CMakeLists.txt               project(... VERSION 0.7.1 ...)
lisp/sel-lang.asd                :version "0.7.1"
```

`python/sel/__init__.py` carries `__version__`, `CHANGELOG.md`'s top heading
carries the version being released, and `composer.json` carries
`extra.branch-alias.dev-main` (`0.7.x-dev`). All are checked against the release
version by `tools/check-version.sh`, so they are places fewer to remember rather
than more — and a release whose notes or branch alias were never updated fails
the check before the tag is cut.

`composer.json` deliberately carries **no** `version` field — Packagist infers
release versions from git tags, and hard-coding it there is a known way to
publish a lie.

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
marker and the licence, about 50 kB. The sdist adds `docs/`, `spec/`,
`conformance/` and the Python examples, mirroring what npm's `files` whitelists.

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

`files` in `package.json` whitelists what ships: `js/`, `docs/`, `spec/`, the JS
examples, the licence and the README. The PHP, C++ and Lisp trees are excluded,
so the tarball is ~73 kB rather than the whole repository.

The package is ESM-only (`"type": "module"`) and exposes one entry point plus the
`sel` CLI:

```js
import { compile, evaluate, Value, SelError } from 'sel-lang';
```

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
      "dev-main": "0.7.x-dev"
    }
  }
```

`tools/check-version.sh` enforces this against the release series, so an outdated branch alias fails the version check before tagging.

### Tag immutability

Never delete, move, or re-tag an existing release tag. Packagist explicitly tracks tag commit hashes and flags moved tags with:

> `Upstream re-tag blocked — Packagist may no longer match the VCS repo for this version`

Once a tag is pushed, it must be treated as immutable. If any fix or correction is needed post-release, cut a new patch release (e.g. `0.7.2`) rather than moving `v0.7.1`.

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
conan upload sel-lang/0.6.0 -r <remote> --confirm
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
`sel.hpp`, `sel_ast.hpp`, `sel.cpp` and `third_party/srell/` and compile" true
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
