# SRELL — vendored, pinned

| | |
|---|---|
| Upstream | https://github.com/upa-url/srell |
| Commit | `7bf06e58cbb312fc4a2ab1cf6c31226fa2475752` |
| Version | 2026.05 |
| Author | Nozomu Katō |
| Licence | BSD 2-Clause — see `LICENSE.txt` |
| File taken | `srell-src/single-header/srell.hpp` → `srell.hpp` |

The source is committed here rather than fetched at build time, so this project
keeps building if the upstream repository disappears. `upa-url/srell` is itself a
mirror of the author's distribution at https://www.akenotsuki.com/misc/srell/.

## Why this and not `std::regex`

SEL guarantees that a rule matches identically on every host (spec/SPEC.md §7.8).
That requires an ECMAScript-conformant engine with code-point semantics, dotall,
and ECMAScript case folding. `std::regex` has none of those: no `u`-mode, no
dotall flag, and it matches over `char`, so offsets would be bytes and `.` would
mean something different from what the JS host means. SRELL *is* an ECMAScript
engine, which is why the C++ host agrees with the JS host by construction rather
than by testing.

## How it is used

`sel.cpp` includes it with `SRELL_NO_UNICODE_PROPERTY` defined. SEL rejects
`\p{...}` at compile time as non-portable, so the Unicode property tables are
dead weight — leaving them out cuts the compile substantially and removes the
only part of the library SEL can never reach.

Matching runs over `std::u32string`, so match offsets are already code point
offsets, which is what SEL reports.

## Updating

Replace `srell.hpp` from a newer tag, update the commit above, and run
`tools/check.sh`. The regex cases in `conformance/09-regex.selt` are what decide
whether the new version still agrees with the other three implementations.
