// Rebuild the whole shipped map through the public registration API, then diff
// what the translator can observe against the map beside it.

use std::collections::HashMap;
use sel_lang::sql::map::{
    entry, lexical, shipped_dialect_names, shipped_lexical_keys, shipped_section_keys, EntryRecord,
};
use sel_lang::sql::{
    define, define_dialect, translate, version, Binding, Bindings, Options, SqlError, SqlKind,
};
use sel_lang::compile;

#[path = "map_replay_data.rs"]
mod map_replay_data;
use map_replay_data::RAW_DIALECTS_JSON;

#[derive(serde::Deserialize)]
struct RawDoc {
    dialect: String,
    extends: Option<String>,
    #[serde(default)]
    version: Option<serde_json::Value>,
    #[serde(default)]
    target: Option<bool>,
    #[serde(default)]
    lexical: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default)]
    ops: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default)]
    funcs: Option<serde_json::Map<String, serde_json::Value>>,
    #[serde(default)]
    skel: Option<serde_json::Map<String, serde_json::Value>>,
}

fn same_entry(a: Option<&EntryRecord>, b: Option<&EntryRecord>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(ra), Some(rb)) => {
            ra.kind == rb.kind
                && ra.reason == rb.reason
                && ra.ret == rb.ret
                && ra.caveat == rb.caveat
                && ra.since == rb.since
                && ra.builder.is_some() == rb.builder.is_some()
                && ra.arity == rb.arity
                && ra.tpl == rb.tpl
                && ra.variants == rb.variants
                && ra.args == rb.args
        }
        _ => false,
    }
}

fn main() {
    let raw: Vec<RawDoc> = match serde_json::from_str(RAW_DIALECTS_JSON) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("failed to parse RawDialectsJSON: {}", e);
            std::process::exit(1);
        }
    };

    const SUF: &str = "~replay";
    let mut calls = 0;

    for doc in &raw {
        let ext = doc.extends.as_ref().map(|s| format!("{}{}", s, SUF));
        let target = doc.target.unwrap_or(true);
        let lex = doc.lexical.clone().unwrap_or_default();

        let mut spec_map = serde_json::Map::new();
        if let Some(e) = ext {
            spec_map.insert("extends".to_string(), serde_json::Value::String(e));
        } else {
            spec_map.insert("extends".to_string(), serde_json::Value::Null);
        }
        if let Some(ref v) = doc.version {
            match v {
                serde_json::Value::String(s) => {
                    spec_map.insert("version".to_string(), serde_json::Value::String(s.clone()));
                }
                serde_json::Value::Number(n) => {
                    spec_map.insert("version".to_string(), serde_json::Value::String(n.to_string()));
                }
                _ => {}
            }
        }
        spec_map.insert("target".to_string(), serde_json::Value::Bool(target));
        spec_map.insert("lexical".to_string(), serde_json::Value::Object(lex));

        define_dialect(&format!("{}{}", doc.dialect, SUF), &serde_json::Value::Object(spec_map));
        calls += 1;

        for sec in &["ops", "funcs", "skel"] {
            let m = match *sec {
                "ops" => &doc.ops,
                "funcs" => &doc.funcs,
                "skel" => &doc.skel,
                _ => unreachable!(),
            };
            if let Some(map) = m {
                for (k, v) in map {
                    define(&format!("{}{}", doc.dialect, SUF), sec, k, v);
                    calls += 1;
                }
            }
        }
    }

    let mut problems = Vec::new();
    let mut compared = 0;

    for name in shipped_dialect_names() {
        compared += 1;
        let replay_name = format!("{}{}", name, SUF);
        if version(&name) != version(&replay_name) {
            problems.push(format!("{}: version {} vs {}", name, version(&name), version(&replay_name)));
        }
        for key in shipped_lexical_keys(&name) {
            compared += 1;
            if lexical(&name, &key) != lexical(&replay_name, &key) {
                problems.push(format!("{}.lexical.{}", name, key));
            }
        }
        for sec in &["ops", "funcs", "skel"] {
            for key in shipped_section_keys(&name, sec) {
                compared += 1;
                let a = entry(&name, sec, &key);
                let b = entry(&replay_name, sec, &key);
                if !same_entry(a.as_ref(), b.as_ref()) {
                    problems.push(format!("{}.{}.{}", name, sec, key));
                }
            }
        }
    }

    let guard_name = format!("mariadb{}~guard", SUF);
    let mut guard_spec = serde_json::Map::new();
    guard_spec.insert("extends".to_string(), serde_json::Value::String(format!("mariadb{}", SUF)));
    let mut guard_lex = serde_json::Map::new();
    guard_lex.insert(
        "numericGuard".to_string(),
        serde_json::Value::String("CASE WHEN ({0} REGEXP '\\\\A-?[0-9]+\\\\z') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END".to_string()),
    );
    guard_spec.insert("lexical".to_string(), serde_json::Value::Object(guard_lex));
    define_dialect(&guard_name, &serde_json::Value::Object(guard_spec));

    let mut refused = false;
    let prog = compile("T == 25").unwrap();
    let mut text_bindings_map = HashMap::new();
    text_bindings_map.insert(
        "T".to_string(),
        Binding::column("t", "", SqlKind::Text, false, false, false, "", "", false),
    );
    let text_col = Bindings::new(Some(text_bindings_map));

    {
        let old_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            translate(&prog, &guard_name, Some(&text_col), Options::default())
        }));
        std::panic::set_hook(old_hook);

        match res {
            Ok(Ok(_)) => {}
            Ok(Err(se)) => {
                problems.push(format!("numericGuard disagreeing with ISNUM raised {} rather than a registration error", se.code));
            }
            Err(_) => {
                refused = true;
            }
        }
    }

    if !refused {
        problems.push("a numericGuard that disagrees with its funcs.ISNUM was accepted; sql/MAP.md rule 10 holds at generation time and not at run time".to_string());
    }

    let memo_name = format!("mariadb{}~memo", SUF);
    let mut memo_spec = serde_json::Map::new();
    memo_spec.insert("extends".to_string(), serde_json::Value::String(format!("mariadb{}", SUF)));
    define_dialect(&memo_name, &serde_json::Value::Object(memo_spec));

    let _ = translate(&prog, &memo_name, Some(&text_col), Options::default());

    let mut isnum_spec = serde_json::Map::new();
    isnum_spec.insert("tpl".to_string(), serde_json::Value::String("({0} REGEXP '^[0-9]+$')".to_string()));
    isnum_spec.insert("ret".to_string(), serde_json::Value::String("BOOL".to_string()));
    define(&memo_name, "funcs", "ISNUM", &serde_json::Value::Object(isnum_spec));

    let mut stale = false;
    {
        let old_hook = std::panic::take_hook();
        std::panic::set_hook(Box::new(|_| {}));
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            translate(&prog, &memo_name, Some(&text_col), Options::default())
        }));
        std::panic::set_hook(old_hook);

        match res {
            Ok(Ok(_)) => {}
            Ok(Err(se)) => {
                problems.push(format!("a redefined ISNUM raised {} rather than a registration error", se.code));
            }
            Err(_) => {
                stale = true;
            }
        }
    }

    if !stale {
        problems.push("an ISNUM redefined after the guard was checked was not noticed; the memo outlived the pairing it vouched for".to_string());
    }

    for p in &problems {
        println!("  DIFFERS {}", p);
    }
    println!(
        "{} registration calls rebuilt the map, {} lookups compared, {} differences (and a disagreeing numericGuard is refused)",
        calls,
        compared,
        problems.len()
    );

    if !problems.is_empty() {
        std::process::exit(1);
    }
}
