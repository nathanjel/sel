// The Go side of the hybrid-parity lane (T11): a line-oriented JSON driver.
//
// Go has no SQLite driver in its standard library, so tools/check-hybrid-parity-go.py
// owns the database: it asks this process to PLAN a program, executes the prefix
// statement itself on a real SQLite, and hands the rows back for the continuation.
// The evaluator, the planner and execute_hybrid are the Go ones; only the rows
// come from elsewhere.
//
//	{"op":"init","bindings":{...}}                       -> {"ok":true}
//	{"op":"direct","sel":S,"data":{...}}                 -> outcome of run()
//	{"op":"plan","sel":S}                                -> {"kind","sql","params"}
//	{"op":"exec","sel":S,"data":{...},"rows":[...]}      -> outcome of execute_hybrid,
//	                                                        plus "mutated": bool
//
// An outcome is {"status":"ok","keys":[...],"rows":[...]} or
// {"status":"err","code":"E_X","line":L,"col":C}; scalars are strings ("NULL",
// "TRUE", "FALSE" for the three that are not text) so hosts compare alike.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

type request struct {
	Op       string          `json:"op"`
	Sel      string          `json:"sel"`
	Bindings json.RawMessage `json:"bindings"`
	Data     json.RawMessage `json:"data"`
	Rows     json.RawMessage `json:"rows"`
}

var bindings *sql.Bindings

func main() {
	// The application functions of sql/oracle/hybrid.json's `application`
	// section: POKE writes its argument in place, HOSTF has no SQL spelling.
	sel.RegisterFunction("POKE", 1, 1, func(args *sel.Args) *sel.Value {
		v := args.Val(0)
		v.Set("k", sel.NewText("9"))
		return v
	})
	sel.RegisterFunction("HOSTF", 1, 1, func(args *sel.Args) *sel.Value { return args.Val(0) })
	in := bufio.NewReaderSize(os.Stdin, 1<<24)
	out := bufio.NewWriter(os.Stdout)
	defer out.Flush()
	for {
		line, err := in.ReadBytes('\n')
		if len(line) > 0 {
			var req request
			if e := json.Unmarshal(line, &req); e != nil {
				reply(out, map[string]interface{}{"status": "err", "code": "HOST:" + e.Error()})
			} else {
				reply(out, handle(&req))
			}
		}
		if err != nil {
			return
		}
	}
}

func reply(out *bufio.Writer, v interface{}) {
	b, _ := json.Marshal(v)
	out.Write(b)
	out.WriteByte('\n')
	out.Flush()
}

func handle(req *request) (res interface{}) {
	defer func() {
		if r := recover(); r != nil {
			res = outcomeOfPanic(r)
		}
	}()
	switch req.Op {
	case "init":
		var spec map[string]struct {
			From   string `json:"from"`
			Alias  string `json:"alias"`
			Fields map[string]struct {
				Table  string `json:"table"`
				Column string `json:"column"`
				Type   string `json:"type"`
			} `json:"fields"`
		}
		if err := json.Unmarshal(req.Bindings, &spec); err != nil {
			panic(err)
		}
		m := map[string]*sql.Binding{}
		for name, b := range spec {
			fields := map[string]*sql.Binding{}
			for f, c := range b.Fields {
				fields[f] = sql.ColumnBinding(c.Column, c.Table, sql.KindFromName(c.Type), false, false, false, "", "", false)
			}
			m[name] = sql.RelationBinding(b.From, b.Alias, fields, "", "", "", false)
		}
		bindings = sql.NewBindings(m)
		return map[string]bool{"ok": true}
	case "direct":
		ctx := decode(req.Data)
		v, err := sel.MustCompile(req.Sel).Run(ctx)
		return outcome(v, err)
	case "plan":
		plan := sql.PlanHybrid(sel.MustCompile(req.Sel), "sqlite", bindings, sql.Options{})
		kind := "hybrid"
		if plan.PureSql {
			kind = "pure_sql"
		} else if plan.PureMemory {
			kind = "pure_memory"
		}
		r := map[string]interface{}{"status": "ok", "kind": kind, "tables": plan.SourceTables}
		if plan.SqlStatement != nil {
			r["sql"] = plan.SqlStatement.AsStatement(sql.ModeParams)
			var params []string
			for _, p := range plan.SqlStatement.Bindings() {
				params = append(params, p.AsText(sel.Pos{}))
			}
			r["params"] = params
		}
		return r
	case "exec":
		program := sel.MustCompile(req.Sel)
		plan := sql.PlanHybrid(program, "sqlite", bindings, sql.Options{})
		ctx := decode(req.Data)
		before := ctx.Dump()
		rows := decode(req.Rows)
		v, err := sql.ExecuteHybrid(plan, func(q string, params []*sel.Value) (*sel.Value, error) { return rows, nil }, ctx)
		r := outcome(v, err)
		r["mutated"] = ctx.Dump() != before
		return r
	}
	return map[string]interface{}{"status": "err", "code": "HOST:unknown op " + req.Op}
}

// decode reads JSON into a Value, keeping object key order (a Go map would not).
func decode(raw json.RawMessage) *sel.Value {
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	return value(dec)
}

func value(dec *json.Decoder) *sel.Value {
	tok, err := dec.Token()
	if err != nil {
		panic(err)
	}
	switch t := tok.(type) {
	case json.Delim:
		switch t {
		case '{':
			var entries []sel.Entry
			for dec.More() {
				k, _ := dec.Token()
				entries = append(entries, sel.Entry{Key: k.(string), Val: value(dec)})
			}
			dec.Token()
			return sel.NewRecordFromEntries(entries)
		case '[':
			var items []*sel.Value
			for dec.More() {
				items = append(items, value(dec))
			}
			dec.Token()
			return sel.NewList(items)
		}
	case string:
		return sel.NewText(t)
	case json.Number:
		return sel.NewText(t.String())
	case bool:
		return sel.NewBool(t)
	case nil:
		return sel.NewNull()
	}
	panic(fmt.Sprintf("unsupported JSON token %v", tok))
}

func scalar(v *sel.Value) string {
	switch {
	case v.IsNull():
		return "NULL"
	case v.IsBool():
		if v.AsBool(sel.Pos{}) {
			return "TRUE"
		}
		return "FALSE"
	}
	return v.AsText(sel.Pos{})
}

func tree(v *sel.Value) interface{} {
	if v.Size() > 0 {
		obj := orderedObject{}
		for _, e := range v.Entries() {
			obj = append(obj, [2]interface{}{e.Key, tree(e.Val)})
		}
		return obj
	}
	return scalar(v)
}

type orderedObject [][2]interface{}

func (o orderedObject) MarshalJSON() ([]byte, error) {
	var b strings.Builder
	b.WriteByte('{')
	for i, kv := range o {
		if i > 0 {
			b.WriteByte(',')
		}
		k, _ := json.Marshal(kv[0])
		v, _ := json.Marshal(kv[1])
		b.Write(k)
		b.WriteByte(':')
		b.Write(v)
	}
	b.WriteByte('}')
	return []byte(b.String()), nil
}

func outcome(v *sel.Value, err error) map[string]interface{} {
	if err != nil {
		return outcomeOfPanic(err)
	}
	keys := []string{}
	rows := []interface{}{}
	for _, e := range v.Entries() {
		keys = append(keys, e.Key)
		rows = append(rows, tree(e.Val))
	}
	return map[string]interface{}{"status": "ok", "keys": keys, "rows": rows}
}

func outcomeOfPanic(r interface{}) map[string]interface{} {
	switch e := r.(type) {
	case *sel.SelError:
		return map[string]interface{}{"status": "err", "code": e.Code, "line": e.Line(), "col": e.Col()}
	case *sql.SqlError:
		return map[string]interface{}{"status": "err", "code": "SQL:" + e.Code}
	case error:
		if se, ok := e.(*sel.SelError); ok {
			return map[string]interface{}{"status": "err", "code": se.Code, "line": se.Line(), "col": se.Col()}
		}
		return map[string]interface{}{"status": "err", "code": "HOST:" + e.Error()}
	}
	return map[string]interface{}{"status": "err", "code": fmt.Sprintf("HOST:%v", r)}
}
