#!/usr/bin/env bash
# The C++ package, built the way a consumer gets it.
#
# Every other C++ layer compiles inside cpp/, where every header is at hand, so
# none of them notices a file the package leaves out. 0.8.1 shipped a CMake
# install whose sel.hpp included a sel_limits.hpp it never installed, and a
# Conan recipe that exported neither the generated headers nor
# sel_optimizer.cpp (which sel.cpp includes): neither compiled for anyone.
#
# This copies exactly the files cpp/conanfile.py exports into an empty
# directory, configures, builds and installs that, then builds
# cpp/test_package against the installed package and runs it.
#
#   tools/check-cpp-package.sh        needs cmake and a C++23 compiler
#   SEL_SKIP_CPP_PACKAGE=1            opts out

set -uo pipefail
cd "$(dirname "$0")/.."

if [ "${SEL_SKIP_CPP_PACKAGE:-}" = "1" ]; then
  echo "cpp package: skipped (SEL_SKIP_CPP_PACKAGE=1)"
  exit 0
fi
if ! command -v cmake >/dev/null 2>&1; then
  echo "cpp package: cmake is not installed; install it or set SEL_SKIP_CPP_PACKAGE=1" >&2
  exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
JOBS="$(( $(nproc 2>/dev/null || echo 2) / 2 ))"; [ "$JOBS" -ge 1 ] || JOBS=1

# The export list, read from the recipe itself so the two cannot drift.
patterns="$(python3 - <<'PY'
import ast, re
src = open('cpp/conanfile.py').read()
m = re.search(r'for pattern in (\(.*?\)):', src, re.S)
print('\n'.join(ast.literal_eval(m.group(1))))
PY
)" || { echo "cpp package: cannot read the export list from cpp/conanfile.py" >&2; exit 1; }

mkdir -p "$WORK/src"
( cd cpp && for p in $patterns; do
    for f in $p; do [ -e "$f" ] && cp -r --parents "$f" "$WORK/src/"; done
  done )
cp LICENSE "$WORK/src/"

if ! cmake -S "$WORK/src" -B "$WORK/build" -DCMAKE_BUILD_TYPE=Release \
       -DCMAKE_INSTALL_PREFIX="$WORK/prefix" > "$WORK/log" 2>&1 \
   || ! cmake --build "$WORK/build" -j "$JOBS" >> "$WORK/log" 2>&1 \
   || ! cmake --install "$WORK/build" >> "$WORK/log" 2>&1; then
  echo "cpp package: the exported sources do not build and install:" >&2
  grep -E 'error:|Error' "$WORK/log" | head -10 >&2
  exit 1
fi

if ! cmake -S cpp/test_package -B "$WORK/consumer" -DCMAKE_PREFIX_PATH="$WORK/prefix" >> "$WORK/log" 2>&1 \
   || ! cmake --build "$WORK/consumer" -j "$JOBS" >> "$WORK/log" 2>&1; then
  echo "cpp package: cpp/test_package does not build against the installed package:" >&2
  grep -E 'error:|Error' "$WORK/log" | head -10 >&2
  exit 1
fi

if ! out="$("$WORK/consumer/example" 2>&1)"; then
  echo "cpp package: cpp/test_package built but failed to run: $out" >&2
  exit 1
fi
echo "cpp package: $(printf '%s\n' $patterns | wc -l) export patterns build, install, and link cpp/test_package"
