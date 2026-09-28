// Rebuild the whole shipped map through the public registration API, then diff
// what the translator can observe against the map beside it.

package main

import (
	"encoding/json"
	"fmt"
	"os"
	"reflect"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

type rawDoc struct {
	Dialect string                 `json:"dialect"`
	Extends *string                `json:"extends"`
	Version string                 `json:"version"`
	Target  *bool                  `json:"target"`
	Lexical map[string]interface{} `json:"lexical"`
	Ops     map[string]interface{} `json:"ops"`
	Funcs   map[string]interface{} `json:"funcs"`
	Skel    map[string]interface{} `json:"skel"`
}

func sameEntry(a, b interface{}) bool {
	if a == nil || b == nil {
		return a == b
	}
	if sa, ok := a.(string); ok {
		sb, ok := b.(string)
		return ok && sa == sb
	}
	ra, okA := a.(*sql.EntryRecord)
	rb, okB := b.(*sql.EntryRecord)
	if !okA || !okB {
		return false
	}
	if ra.Kind != rb.Kind || ra.Reason != rb.Reason || ra.Ret != rb.Ret || ra.Caveat != rb.Caveat || ra.Since != rb.Since {
		return false
	}
	if (ra.Builder == nil) != (rb.Builder == nil) {
		return false
	}
	if (ra.Arity == nil) != (rb.Arity == nil) {
		return false
	}
	if ra.Arity != nil && *ra.Arity != *rb.Arity {
		return false
	}
	if !reflect.DeepEqual(ra.Tpl, rb.Tpl) {
		return false
	}
	if !reflect.DeepEqual(ra.Variants, rb.Variants) {
		return false
	}
	if !reflect.DeepEqual(ra.Args, rb.Args) {
		return false
	}
	return true
}

func sameLexical(a, b interface{}) bool {
	return reflect.DeepEqual(a, b)
}

func main() {
	var raw []rawDoc
	if err := json.Unmarshal([]byte(RawDialectsJSON), &raw); err != nil {
		fmt.Fprintf(os.Stderr, "failed to parse RawDialectsJSON: %v\n", err)
		os.Exit(1)
	}

	const SUF = "~replay"
	calls := 0

	for _, doc := range raw {
		var ext *string
		if doc.Extends != nil {
			s := *doc.Extends + SUF
			ext = &s
		}
		target := true
		if doc.Target != nil {
			target = *doc.Target
		}
		lex := doc.Lexical
		if lex == nil {
			lex = make(map[string]interface{})
		}
		sql.DefineDialect(doc.Dialect+SUF, map[string]interface{}{
			"extends": ext,
			"version": doc.Version,
			"target":  target,
			"lexical": lex,
		})
		calls++

		for _, sec := range []string{"ops", "funcs", "skel"} {
			var m map[string]interface{}
			switch sec {
			case "ops":
				m = doc.Ops
			case "funcs":
				m = doc.Funcs
			case "skel":
				m = doc.Skel
			}
			for k, v := range m {
				sql.Define(doc.Dialect+SUF, sec, k, v)
				calls++
			}
		}
	}

	var problems []string
	compared := 0

	for _, name := range sql.ShippedDialectNames() {
		compared++
		if sql.Version(name) != sql.Version(name+SUF) {
			problems = append(problems, fmt.Sprintf("%s: version %s vs %s", name, sql.Version(name), sql.Version(name+SUF)))
		}
		for _, key := range sql.ShippedLexicalKeys(name) {
			compared++
			if !sameLexical(sql.Lexical(name, key), sql.Lexical(name+SUF, key)) {
				problems = append(problems, fmt.Sprintf("%s.lexical.%s", name, key))
			}
		}
		for _, sec := range []string{"ops", "funcs", "skel"} {
			for _, key := range sql.ShippedSectionKeys(name, sec) {
				compared++
				if !sameEntry(sql.Entry(name, sec, key), sql.Entry(name+SUF, sec, key)) {
					problems = append(problems, fmt.Sprintf("%s.%s.%s", name, sec, key))
				}
			}
		}
	}

	guardName := fmt.Sprintf("mariadb%s~guard", SUF)
	sql.DefineDialect(guardName, map[string]interface{}{
		"extends": fmt.Sprintf("mariadb%s", SUF),
		"lexical": map[string]interface{}{
			"numericGuard": "CASE WHEN ({0} REGEXP '\\\\A-?[0-9]+\\\\z') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END",
		},
	})
	refused := false
	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sql.SqlError); ok {
					problems = append(problems, fmt.Sprintf("numericGuard disagreeing with ISNUM raised %s rather than a registration error", se.Code))
				} else {
					refused = true
				}
			}
		}()
		sql.MustTranslate(sel.MustCompile("T == 25"), guardName, sql.NewBindings(map[string]*sql.Binding{
			"T": sql.ColumnBinding("t", "", sql.KindText, false, false, false, "", "", false),
		}), sql.Options{})
	}()
	if !refused {
		problems = append(problems, "a numericGuard that disagrees with its funcs.ISNUM was accepted; sql/MAP.md rule 10 holds at generation time and not at run time")
	}

	memoName := fmt.Sprintf("mariadb%s~memo", SUF)
	sql.DefineDialect(memoName, map[string]interface{}{
		"extends": fmt.Sprintf("mariadb%s", SUF),
	})
	prog := sel.MustCompile("T == 25")
	textCol := sql.NewBindings(map[string]*sql.Binding{
		"T": sql.ColumnBinding("t", "", sql.KindText, false, false, false, "", "", false),
	})
	sql.MustTranslate(prog, memoName, textCol, sql.Options{})
	sql.Define(memoName, "funcs", "ISNUM", map[string]interface{}{
		"tpl": "({0} REGEXP '^[0-9]+$')",
		"ret": "BOOL",
	})
	stale := false
	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sql.SqlError); ok {
					problems = append(problems, fmt.Sprintf("a redefined ISNUM raised %s rather than a registration error", se.Code))
				} else {
					stale = true
				}
			}
		}()
		sql.MustTranslate(prog, memoName, textCol, sql.Options{})
	}()
	if !stale {
		problems = append(problems, "an ISNUM redefined after the guard was checked was not noticed; the memo outlived the pairing it vouched for")
	}

	for _, p := range problems {
		fmt.Printf("  DIFFERS %s\n", p)
	}
	fmt.Printf("%d registration calls rebuilt the map, %d lookups compared, %d differences (and a disagreeing numericGuard is refused)\n", calls, compared, len(problems))
	if len(problems) > 0 {
		os.Exit(1)
	}
}
