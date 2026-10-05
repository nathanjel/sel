// This host's side of tools/check-hybrid-parity-driver.py: the corpus in
// sql/oracle/hybrid.json is planned and run HERE, and the harness supplies the
// one thing this host has no built-in client for, a database. One JSON request
// per line on stdin, one JSON answer per line on stdout -- the protocol of
// lisp/bin/hybrid-driver.lisp:
//
//   {"op":"init","bindings":{"ORDERS":{"from":"orders","alias":"o",
//                                     "fields":{"ID":{"column":"id","table":"o","type":"NUM"}}}}}
//   {"op":"run","sel":"...","vars":{...}}
//       -> {"status":"ok","native":<json>,"keys":[...]} | {"status":"err","code":"E_X","line":1,"col":2}
//   {"op":"plan","sel":"...","dialect":"sqlite"}
//       -> {"kind":"pure_sql|hybrid|pure_memory","statement":"..."}
//   {"op":"plan-statement","sel":"...","dialect":"sqlite"}
//       -> {"sql":"... ? ...","params":[...]}       the statement the plan sends
//   {"op":"execute","sel":"...","dialect":"sqlite","vars":{...},"rows":<json>}
//       -> the answer of `run`, plus "before"/"after": the dump of the caller's
//          context around the call
//
// A value crosses as JSON: an object is a record, an array a list, a string
// text, a number a SEL number spelt as written, null none. Answers come back
// with every scalar as text, TRUE/FALSE for a boolean.
//
// The application functions of the corpus's `application` section are
// registered before anything is compiled: POKE(x) writes TEXT "9" at key "k" of
// the value it receives, in place, and returns it; HOSTF(x) returns x.

use sel_lang::sql::{
    execute_hybrid, plan_hybrid, Binding, Bindings, FieldEntry, HybridPlan, Mode, Options, PlanKind,
    SqlKind,
};
use sel_lang::{compile, dec_parse, register_function, Entry, Kind, Pos, SelError, Value};
use serde_json::{json, Map, Value as Json};
use std::collections::HashMap;
use std::io::{self, BufRead, Write};

fn to_value(j: &Json) -> Result<Value, SelError> {
    Ok(match j {
        Json::Null => Value::none(),
        Json::Bool(b) => Value::bool(*b),
        Json::String(s) => Value::text_owned(s.clone()),
        Json::Number(n) => Value::num(dec_parse(&n.to_string(), Pos::default())?)?,
        Json::Array(items) => Value::list(items.iter().map(to_value).collect::<Result<_, _>>()?),
        Json::Object(fields) => Value::record_from_entries(
            fields
                .iter()
                .map(|(k, v)| Ok(Entry { key: k.clone(), val: to_value(v)? }))
                .collect::<Result<_, SelError>>()?,
        ),
    })
}

fn to_json(v: &Value) -> Json {
    if v.size() > 0 {
        let mut out = Map::new();
        for k in v.keys() {
            out.insert(k.clone(), to_json(&v.get(&k).unwrap_or_else(Value::none)));
        }
        return Json::Object(out);
    }
    match v.kind() {
        Kind::None => Json::Object(Map::new()),
        Kind::Bool => json!(if v.as_bool(Pos::default()).unwrap_or(false) { "TRUE" } else { "FALSE" }),
        _ => Json::String(v.scalar()),
    }
}

fn outcome(result: Result<Value, SelError>) -> Json {
    match result {
        Ok(v) => json!({"status": "ok", "native": to_json(&v), "keys": v.keys()}),
        Err(e) => json!({"status": "err", "code": e.code, "line": e.pos.line, "col": e.pos.col}),
    }
}

fn kind_of(name: &str) -> SqlKind {
    match name.to_ascii_uppercase().as_str() {
        "NUM" => SqlKind::Num,
        "TEXT" => SqlKind::Text,
        "BOOL" => SqlKind::Bool,
        "BIN" => SqlKind::Bin,
        _ => SqlKind::Unknown,
    }
}

fn bindings_from(spec: &Json) -> Bindings {
    let str_of = |j: &Json, key: &str| j.get(key).and_then(Json::as_str).unwrap_or("").to_string();
    let mut map = HashMap::new();
    for (name, b) in spec.as_object().into_iter().flatten() {
        let fields = b
            .get("fields")
            .and_then(Json::as_object)
            .into_iter()
            .flatten()
            .map(|(fname, c)| {
                let column = Binding::column(
                    &str_of(c, "column"), &str_of(c, "table"), kind_of(&str_of(c, "type")),
                    false, false, false, "", "", false,
                );
                FieldEntry::new(fname.clone(), column)
            })
            .collect();
        let relation = Binding::relation(&str_of(b, "from"), &str_of(b, "alias"), fields, "", "", "", false);
        map.insert(name.clone(), relation);
    }
    Bindings::new(Some(map))
}

/// A configuration panic (a SqlError the planner raises) as an error answer.
fn guarded(f: impl FnOnce() -> Json) -> Json {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or_else(|e| {
        let what = e
            .downcast_ref::<sel_lang::sql::SqlError>()
            .map(|s| format!("SQL:{}", s.code))
            .unwrap_or_else(|| "HOST:panic".to_string());
        json!({"status": "err", "code": what, "line": 0, "col": 0})
    })
}

fn plan(req: &Json, bindings: &Bindings) -> Result<HybridPlan, SelError> {
    let program = compile(req["sel"].as_str().unwrap_or(""))?;
    Ok(plan_hybrid(&program, req["dialect"].as_str().unwrap_or("sqlite"), Some(bindings), Options::default()))
}

fn err_json(e: SelError) -> Json {
    json!({"status": "err", "code": e.code, "line": e.pos.line, "col": e.pos.col})
}

fn handle(req: &Json, bindings: &mut Bindings) -> Json {
    match req["op"].as_str().unwrap_or("") {
        "init" => {
            *bindings = bindings_from(&req["bindings"]);
            json!({"status": "ok"})
        }
        "run" => outcome(
            compile(req["sel"].as_str().unwrap_or(""))
                .and_then(|mut p| p.run(Some(to_value(&req["vars"])?))),
        ),
        "plan" => guarded(|| match plan(req, bindings) {
            Ok(p) => {
                let kind = match p.kind() {
                    PlanKind::PureSql => "pure_sql",
                    PlanKind::PureMemory => "pure_memory",
                    PlanKind::Hybrid => "hybrid",
                };
                let statement = p
                    .sql_statement
                    .as_ref()
                    .and_then(|f| f.as_statement(Mode::Inline).ok())
                    .unwrap_or_else(|| "-".to_string());
                json!({"kind": kind, "statement": statement})
            }
            Err(e) => err_json(e),
        }),
        "plan-statement" => guarded(|| match plan(req, bindings) {
            Ok(p) => match p.sql_statement.as_ref() {
                Some(f) => json!({
                    "sql": f.as_statement(Mode::Params).unwrap_or_default(),
                    "params": f.bindings().iter()
                        .map(|v| if v.kind() == Kind::None && v.size() == 0 { Json::Null } else { Json::String(v.scalar()) })
                        .collect::<Vec<_>>(),
                }),
                None => json!({"sql": null, "params": []}),
            },
            Err(e) => err_json(e),
        }),
        "execute" => guarded(|| {
            let p = match plan(req, bindings) {
                Ok(p) => p,
                Err(e) => return err_json(e),
            };
            let caller = match to_value(&req["vars"]) {
                Ok(v) => v,
                Err(e) => return err_json(e),
            };
            let before = caller.dump().unwrap_or_default();
            let rows = to_value(&req["rows"]);
            let mut answer = outcome(rows.and_then(|rows| {
                execute_hybrid(&p, |_, _| Ok(rows.clone()), Some(&caller))
            }));
            answer["before"] = json!(before);
            answer["after"] = json!(caller.dump().unwrap_or_default());
            answer
        }),
        _ => json!({"status": "err", "code": "HOST:unknown-op"}),
    }
}

fn main() {
    // A refusal surfaces as an answer, not as a panic message on stderr.
    std::panic::set_hook(Box::new(|_| {}));
    register_function("POKE", 1, 1, |args| {
        let v = args.val(0)?;
        v.set("k", Value::text_owned("9".into()), Pos::default())?;
        Ok(v)
    })
    .expect("POKE registers");
    register_function("HOSTF", 1, 1, |args| args.val(0)).expect("HOSTF registers");

    let mut bindings = Bindings::default();
    let stdin = io::stdin();
    let mut stdout = io::stdout().lock();
    for line in stdin.lock().lines() {
        let Ok(line) = line else { break };
        let answer = match serde_json::from_str::<Json>(&line) {
            Ok(req) => handle(&req, &mut bindings),
            Err(e) => json!({"status": "err", "code": format!("HOST:bad JSON: {e}")}),
        };
        if writeln!(stdout, "{answer}").and_then(|_| stdout.flush()).is_err() {
            break;
        }
    }
}
