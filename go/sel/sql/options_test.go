package sql

import (
	"testing"

	"github.com/nathanjel/sel/go/sel"
)

// The typed constructors build what the positional ones build.
func TestTypedConfigurationMatchesPositional(t *testing.T) {
	defer Reset()
	no := false
	DefineDialectWith("typed-probe", DialectOptions{Extends: "mariadb", Version: "11.4", Target: &no})
	DefineDialect("positional-probe", map[string]interface{}{"extends": "mariadb", "version": "11.4", "target": false})
	if Version("typed-probe") != Version("positional-probe") || Exists("typed-probe") != Exists("positional-probe") {
		t.Fatal("typed and positional dialect declarations differ")
	}
	typed := NewBindings(map[string]*Binding{
		"NAME":   Column("name", "c", KindText, ColumnOptions{Collation: "binary"}),
		"AMOUNT": RawColumn("o.amount", KindNum, ColumnOptions{Guard: true}),
		"ORDERS": Relation("orders", "o", []FieldEntry{{Name: "ID", Binding: Column("id", "o", KindNum, ColumnOptions{})}}, RelationOptions{}),
	})
	positional := NewBindings(map[string]*Binding{
		"NAME":   ColumnBinding("name", "c", KindText, false, false, false, "binary", "", false),
		"AMOUNT": RawBinding("o.amount", KindNum, false, false, true, "", "", false),
		"ORDERS": RelationBinding("orders", "o", []FieldEntry{{Name: "ID", Binding: ColumnBinding("id", "o", KindNum, false, false, false, "", "", false)}}, "", "", "", false),
	})
	for _, src := range []string{`NAME $== "x" AND AMOUNT > 3`, `COUNT(ORDERS .> FILTER(_["id"] > 2)) > 1`} {
		a, errA := Translate(sel.MustCompile(src), "mariadb", typed, Options{})
		b, errB := Translate(sel.MustCompile(src), "mariadb", positional, Options{})
		if errA != nil || errB != nil {
			t.Fatalf("%s: %v / %v", src, errA, errB)
		}
		if a.AsCondition(ModeInline) != b.AsCondition(ModeInline) {
			t.Errorf("%s:\n typed      %s\n positional %s", src, a.AsCondition(ModeInline), b.AsCondition(ModeInline))
		}
	}
}
