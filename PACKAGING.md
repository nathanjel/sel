# Publishing

The package is called **`sel-lang`** on every registry. `sel` was already taken
in three of the four: an npm CSS-selector library, and a `projects/sel` entry in
quicklisp-projects (GrammaTech's Software Evolution Library).

| Registry | Name | Manifest |
|---|---|---|
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
tools/check.sh          # must print ALL GREEN with all four implementations
```

Then tag. Every registry below either reads the tag or is told the version by
hand, and they must agree:

```
git tag -a v0.1.1 -m "SEL 0.1.1"
git push origin v0.1.1
```

Versions live in five places. Keep them in step:

```
package.json                     "version": "0.1.1"
cpp/conanfile.py                 version = "0.1.1"
cpp/vcpkg.json                   "version-semver": "0.1.1"
cpp/CMakeLists.txt               project(... VERSION 0.1.1 ...)
lisp/sel-lang.asd                :version "0.1.1"
```

`composer.json` deliberately carries **no** `version` field — Packagist infers it
from the git tag, and hard-coding it there is a known way to publish a lie.

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

## Conan

Conan Center does **not** have SRELL, so the vendored copy is what makes the
recipe build at all. See the note below.

```
conan create cpp/ --build=missing
```

To publish, either upload to your own remote:

```
conan upload sel-lang/0.1.1 -r <remote> --confirm
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
| plain CMake / copy-two-files | n/a | vendored |

The vendored copy is the default everywhere, on purpose. It is what keeps "copy
`sel.hpp`, `sel.cpp` and `third_party/srell/` and compile" true, it is the only
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
