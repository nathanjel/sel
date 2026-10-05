#!/usr/bin/env bash
# The Go module, built the way `go get` delivers it.
#
# A Go module is published by tagging the repository (go/vX.Y.Z, because the
# module is the go/ directory), and the module proxy zips go/ as that tag has
# it: nothing outside go/, nothing git ignores. Every other Go layer builds
# inside the checkout, so none of them notices a file the zip would lack. This
# copies exactly the files git would commit under go/ into an empty directory
# and, from that copy alone:
#
#   - checks what pkg.go.dev needs: a LICENSE (the repository's, byte for
#     byte), a README.md, the package documentation of sel and sel/sql, and a
#     go.mod with no replace and no requirements (the module has none);
#   - builds and vets every package, and runs the documentation's examples;
#   - builds and runs a consumer module against it, as the README shows;
#   - installs the `sel` command (go install ./bin/sel) and runs it.
#
#   tools/check-go-module.sh        needs go
#   SEL_SKIP_GO_MODULE=1            opts out

set -uo pipefail
cd "$(dirname "$0")/.."

if [ "${SEL_SKIP_GO_MODULE:-}" = "1" ]; then
  echo "go module: skipped (SEL_SKIP_GO_MODULE=1)"
  exit 0
fi
command -v go >/dev/null || { echo "go module: go is not installed" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT
status=0
fail() { echo "FAIL go module: $*"; status=1; }

# What a commit of go/ would hold: tracked files, and new ones git does not ignore.
mod="$WORK/module"
mkdir -p "$mod"
git ls-files -z --cached --others --exclude-standard -- go \
  | (cd go/.. && xargs -0 -r tar -cf - --no-recursion) | tar -xf - -C "$WORK"
mv "$WORK/go" "$mod.tmp" && rmdir "$mod" && mv "$mod.tmp" "$mod"

# What the proxy and pkg.go.dev need.
cmp -s "$mod/LICENSE" LICENSE || fail "go/LICENSE is missing or not the repository's LICENSE"
[ -s "$mod/README.md" ] || fail "go/README.md is missing"
[ "$(sed -n 's/^module //p' "$mod/go.mod")" = "github.com/nathanjel/sel/go" ] || fail "go.mod names another module"
grep -Eq '^(replace|require)' "$mod/go.mod" && fail "go.mod has a replace or a requirement; the module has neither"
for pkg in sel sel/sql; do
  (cd "$mod" && go doc "./$pkg" 2>/dev/null | grep -q "^Package ${pkg##*/} ") || fail "$pkg has no package documentation"
done

# The hand-written sources are gofmt-clean. The generated ones (*_gen.go and the
# three manifests under internal/) are left out until their generators emit
# gofmt's layout; the generators, not gofmt -w, decide those bytes
# (tools/check-generated.sh).
unformatted="$(cd "$mod" && gofmt -l . | grep -v -e '_gen\.go$' -e '^internal/limits/limits\.go$' \
  -e '^internal/manifest/builtins\.go$' -e '^internal/mathops/math_ops\.go$')"
[ -z "$unformatted" ] || fail "not gofmt-clean: $(echo $unformatted)"

# Every package builds and vets, and the examples pkg.go.dev shows still print
# what they say.
export GOFLAGS=-mod=mod GOTOOLCHAIN=local
if ! (cd "$mod" && go build ./... && go vet ./... && go test -run '^Example' ./sel/...) > "$WORK/build.log" 2>&1; then
  fail "the module does not build, vet or pass its examples on its own"
  tail -20 "$WORK/build.log" | sed 's/^/       /'
fi

# A consumer, against the copy only.
mkdir -p "$WORK/consumer"
cat > "$WORK/consumer/go.mod" <<EOF
module consumer

go 1.22

require github.com/nathanjel/sel/go v0.0.0

replace github.com/nathanjel/sel/go => $mod
EOF
cat > "$WORK/consumer/main.go" <<'EOF'
package main

import (
	"errors"
	"fmt"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

func main() {
	rule := sel.MustCompile(`IF(QTY * PRICE > LIMIT, "over budget", "ok")`)
	ctx := sel.NewNone()
	ctx.Set("QTY", sel.NewText("3"))
	ctx.Set("PRICE", sel.NewText("19.99"))
	ctx.Set("LIMIT", sel.NewText("50.00"))
	v, err := rule.Run(ctx)
	if err != nil {
		panic(err)
	}
	fmt.Println(v.AsText(sel.Pos{}))
	_, err = sel.Eval(`3 + "A"`, nil)
	var e *sel.SelError
	if errors.As(err, &e) {
		fmt.Printf("%s at %d:%d\n", e.Code, e.Line(), e.Col())
	}
	fmt.Println(strings.Join(sel.MustCompile("A + B").Dependencies(), " "))
	b := sql.NewBindings(map[string]*sql.Binding{
		"QTY": sql.ColumnBinding("qty", "t", sql.KindNum, false, false, false, "", "", false),
	})
	f, err := sql.Translate(sel.MustCompile("QTY > 5"), "sqlite", b, sql.Options{})
	if err != nil {
		panic(err)
	}
	fmt.Println(f.AsCondition(sql.ModeInline))
}
EOF
want='over budget
E_NOT_NUM at 1:5
A B
(CAST("t"."qty" AS NUMERIC) > CAST('"'"'5'"'"' AS NUMERIC))'
if got="$(cd "$WORK/consumer" && go run . 2> "$WORK/consumer.err")"; then
  [ "$got" = "$want" ] || { fail "the consumer printed something else:"; diff <(echo "$want") <(echo "$got") | sed 's/^/       /'; }
else
  fail "the consumer does not build against the module"
  tail -20 "$WORK/consumer.err" | sed 's/^/       /'
fi

# The command a user installs.
if (cd "$mod" && GOBIN="$WORK/bin" go install ./bin/sel) > "$WORK/install.log" 2>&1; then
  out="$("$WORK/bin/sel" -e '2.50 + 2.50' 2>&1)"
  [ "$out" = "5.00" ] || fail "the installed sel printed '$out' for 2.50 + 2.50"
else
  fail "go install ./bin/sel failed"
  tail -20 "$WORK/install.log" | sed 's/^/       /'
fi

[ "$status" -eq 0 ] && echo "go module: github.com/nathanjel/sel/go — $(find "$mod" -type f | wc -l) files, docs, examples, consumer and CLI all good"
exit "$status"
