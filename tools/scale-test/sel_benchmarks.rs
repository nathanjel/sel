// Rust scale/parity runner: sel_benchmarks.cpp transcribed, with the same
// oracle (benchmark_results.json -- rows, SQL and hybrid shape), the same
// prepared context, the same two host functions, the same schema_version 2
// report, and the same timing contract: prepared_total_ms is run + dump;
// preparing the context (the isolated deep copy) is reported apart and excluded.
//
// Built as the crate's `scale-bench` example (rust/Cargo.toml) and run by
// tools/scale-test/benchmark_all.py, whose validate_report() gates it exactly
// as it gates the other hosts.

use sel_lang::sql::{plan_hybrid, Binding, Bindings, FieldEntry, Mode, Options, SqlKind};
use sel_lang::{compile, dec_parse, register_function, Entry, Pos, Program, SelError, Value};
use serde_json::{json, Value as Json};
use std::collections::HashMap;
use std::path::Path;
use std::process::Command;
use std::time::Instant;

type Failure = Box<dyn std::error::Error>;

// JSON as the C++ runner reads it. serde_json::Value would sort object keys,
// and a SEL record keeps its keys in order -- the reference rows compare that
// order, and the fixture's rows have it -- so objects are read into a list.
enum Json2 {
    Null,
    Bool(bool),
    Num(String),
    Str(String),
    Arr(Vec<Json2>),
    Obj(Vec<(String, Json2)>),
}

impl<'de> serde::Deserialize<'de> for Json2 {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = Json2;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
                f.write_str("JSON")
            }
            fn visit_unit<E>(self) -> Result<Json2, E> { Ok(Json2::Null) }
            fn visit_bool<E>(self, b: bool) -> Result<Json2, E> { Ok(Json2::Bool(b)) }
            fn visit_i64<E>(self, n: i64) -> Result<Json2, E> { Ok(Json2::Num(n.to_string())) }
            fn visit_u64<E>(self, n: u64) -> Result<Json2, E> { Ok(Json2::Num(n.to_string())) }
            fn visit_f64<E>(self, n: f64) -> Result<Json2, E> { Ok(Json2::Num(n.to_string())) }
            fn visit_str<E>(self, s: &str) -> Result<Json2, E> { Ok(Json2::Str(s.to_string())) }
            fn visit_string<E>(self, s: String) -> Result<Json2, E> { Ok(Json2::Str(s)) }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut a: A) -> Result<Json2, A::Error> {
                let mut xs = Vec::new();
                while let Some(x) = a.next_element()? {
                    xs.push(x);
                }
                Ok(Json2::Arr(xs))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut a: A) -> Result<Json2, A::Error> {
                let mut xs = Vec::new();
                while let Some(entry) = a.next_entry()? {
                    xs.push(entry);
                }
                Ok(Json2::Obj(xs))
            }
        }
        d.deserialize_any(V)
    }
}

impl Json2 {
    fn field(&self, key: &str) -> Option<&Json2> {
        match self {
            Json2::Obj(xs) => xs.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    fn text(&self) -> Option<&str> {
        match self {
            Json2::Str(s) => Some(s),
            _ => None,
        }
    }

    fn len(&self) -> usize {
        match self {
            Json2::Arr(xs) => xs.len(),
            Json2::Obj(xs) => xs.len(),
            _ => 0,
        }
    }

    // Integers as numbers, other numbers as decimals, null as NONE and an
    // empty object as NONE: the C++ runner's reader.
    fn to_value(&self) -> Result<Value, SelError> {
        Ok(match self {
            Json2::Null => Value::none(),
            Json2::Bool(b) => Value::bool(*b),
            Json2::Num(t) if !t.contains(['.', 'e', 'E']) => match t.parse::<i64>() {
                Ok(n) => Value::int(n),
                Err(_) => Value::text_owned(t.clone()),
            },
            Json2::Num(t) => match dec_parse(t, Pos::default()) {
                Ok(d) => Value::num(d)?,
                Err(_) => Value::text_owned(t.clone()),
            },
            Json2::Str(s) => Value::text_owned(s.clone()),
            Json2::Arr(xs) => Value::list(xs.iter().map(Json2::to_value).collect::<Result<_, _>>()?),
            Json2::Obj(xs) if xs.is_empty() => Value::none(),
            Json2::Obj(xs) => Value::record_from_entries(
                xs.iter()
                    .map(|(k, v)| Ok(Entry { key: k.clone(), val: v.to_value()? }))
                    .collect::<Result<_, SelError>>()?,
            ),
        })
    }
}

fn read_json(path: &str) -> Result<Json2, Failure> {
    let bytes = std::fs::read(path).map_err(|e| format!("cannot read {path}: {e}"))?;
    Ok(serde_json::from_slice(&bytes)?)
}

// std::stoll on the text before its '.', or the fallback.
fn whole(text: &str, fallback: i64) -> i64 {
    let head = text.split('.').next().unwrap_or("").trim_start();
    let (sign, digits) = match head.as_bytes().first() {
        Some(b'-') => (-1, &head[1..]),
        Some(b'+') => (1, &head[1..]),
        _ => (1, head),
    };
    let n: String = digits.chars().take_while(|c| c.is_ascii_digit()).collect();
    n.parse::<i64>().map(|v| sign * v).unwrap_or(fallback)
}

fn register_benchmark_builtins() -> Result<(), SelError> {
    register_function("CUSTOM_VIP_SCORE", 2, 2, |a| {
        let tier = a.text(0)?;
        let year = whole(&a.text(1)?, 2024);
        let base = match tier.as_str() {
            "PLATINUM" => 100,
            "GOLD" => 50,
            "SILVER" => 25,
            _ => 10,
        };
        Ok(Value::int(base + (2026 - year) * 5))
    })?;
    register_function("HOST_RISK_SCORE", 2, 2, |a| {
        let country = a.text(0)?;
        Ok(Value::int((if country == "US" { 30 } else { 10 }) + whole(&a.text(1)?, 0) * 2))
    })
}

fn column(name: &str, table: &str, kind: SqlKind) -> Binding {
    Binding::column(name, table, kind, false, false, false, "", "", false)
}

fn relation(table: &str, fields: &[(&str, &str)]) -> Binding {
    let mapped = fields
        .iter()
        .map(|&(key, name)| {
            let text = matches!(key, "CODE" | "NAME" | "SKU" | "COUNTRY" | "TIER" | "STATUS");
            FieldEntry::new(key, column(name, table, if text { SqlKind::Text } else { SqlKind::Num }))
        })
        .collect();
    Binding::relation(table, table, mapped, "", "", "", false)
}

fn schema(dialect: &str) -> Bindings {
    let categories = relation("categories", &[("ID", "id"), ("CODE", "code"), ("NAME", "name"), ("VAT_RATE", "vat_rate")]);
    let products = relation("products", &[
        ("ID", "id"), ("SKU", "sku"), ("NAME", "name"), ("CATEGORY_ID", "category_id"),
        ("PRICE", "price"), ("IS_ACTIVE", "is_active")]);
    let distance = if dialect == "postgresql" {
        "ROUND((customers.location <-> point(13.404954, 52.520008))::numeric, 6)"
    } else {
        "ROUND(ST_Distance(POINT(customers.longitude, customers.latitude), POINT(13.404954, 52.520008)), 6)"
    };
    let customers = Binding::relation("customers", "customers", vec![
        FieldEntry::new("ID", column("id", "customers", SqlKind::Num)),
        FieldEntry::new("NAME", column("name", "customers", SqlKind::Text)),
        FieldEntry::new("TIER", column("tier", "customers", SqlKind::Text)),
        FieldEntry::new("COUNTRY", column("country", "customers", SqlKind::Text)),
        FieldEntry::new("CREATED_YEAR", column("created_year", "customers", SqlKind::Num)),
        FieldEntry::new("LATITUDE", column("latitude", "customers", SqlKind::Num)),
        FieldEntry::new("LONGITUDE", column("longitude", "customers", SqlKind::Num)),
        FieldEntry::new("DIST_BERLIN", Binding::raw(distance, SqlKind::Num, false, false, false, "", "", false)),
    ], "", "", "", false);
    let orders = relation("orders", &[
        ("ID", "id"), ("CUSTOMER_ID", "customer_id"), ("STATUS", "status"),
        ("DISCOUNT", "discount"), ("ORDER_YEAR", "order_year")]);
    let items = relation("order_items", &[
        ("ID", "id"), ("ORDER_ID", "order_id"), ("PRODUCT_ID", "product_id"),
        ("QUANTITY", "quantity"), ("UNIT_PRICE", "unit_price")]);
    let mut map = HashMap::new();
    map.insert("CATEGORIES".to_string(), categories);
    map.insert("PRODUCTS".to_string(), products);
    map.insert("CUSTOMERS".to_string(), customers);
    map.insert("ORDERS".to_string(), orders);
    map.insert("ORDER_ITEMS".to_string(), items);
    Bindings::new(Some(map))
}

// Each customer gains dist_berlin, as the C++ runner computes it: the
// coordinates through std::stof (a float), the distance in double, six places.
fn add_distances(context: &Value) -> Result<(), Failure> {
    let Some(customers) = context.get("CUSTOMERS") else { return Ok(()) };
    let mut shaped = Vec::with_capacity(customers.size());
    for Entry { val: customer, .. } in customers.elements() {
        let coordinate = |key: &str| -> Result<f64, Failure> {
            let text = customer.get(key).ok_or(format!("customer has no {key}"))?.as_text(Pos::default())?;
            Ok(text.trim().parse::<f32>()? as f64)
        };
        let dx = coordinate("longitude")? - 13.404954;
        let dy = coordinate("latitude")? - 52.520008;
        let distance = format!("{:.6}", (dx * dx + dy * dy).sqrt());
        let mut entries = customer.entries();
        entries.push(Entry { key: "dist_berlin".into(), val: Value::num(dec_parse(&distance, Pos::default())?)? });
        shaped.push(Value::record_from_entries(entries));
    }
    context.set("CUSTOMERS", Value::list(shaped), Pos::default())?;
    Ok(())
}

#[derive(Default)]
struct RepresentationCounts {
    shaped_records: usize,
    fallback_records: usize,
    lists: usize,
}

fn count_representation(value: &Value, counts: &mut RepresentationCounts) {
    let inner = value.inner();
    if inner.is_list {
        counts.lists += 1;
    } else if inner.shape.is_some() {
        counts.shaped_records += 1;
    } else if !inner.entries().is_empty() {
        counts.fallback_records += 1;
    }
    let children: Vec<Value> = match &inner.storage {
        Some(storage) => storage.clone(),
        None => inner.entries().iter().map(|e| e.val.clone()).collect(),
    };
    drop(inner);
    for child in &children {
        count_representation(child, counts);
    }
}

fn sha256(path: &str) -> String {
    Command::new("sha256sum")
        .arg(path)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .and_then(|s| s.split_whitespace().next().map(str::to_string))
        .unwrap_or_default()
}

fn command_line(program: &str, arg: &str) -> String {
    Command::new(program)
        .arg(arg)
        .output()
        .ok()
        .and_then(|o| String::from_utf8(o.stdout).ok())
        .map(|s| s.trim().to_string())
        .unwrap_or_else(|| "unknown".into())
}

fn cpu_model() -> String {
    std::fs::read_to_string("/proc/cpuinfo")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with("model name"))
                .and_then(|line| line.split_once(':'))
                .map(|(_, v)| v.trim().to_string())
        })
        .unwrap_or_else(|| "unknown".into())
}

fn available_memory_bytes() -> u64 {
    std::fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|text| {
            text.lines()
                .find(|line| line.starts_with("MemAvailable:"))
                .and_then(|line| line.split_whitespace().nth(1))
                .and_then(|kb| kb.parse::<u64>().ok())
        })
        .map_or(0, |kb| kb * 1024)
}

fn arg(args: &[String], name: &str, fallback: &str) -> String {
    args.windows(2)
        .find(|w| w[0] == name)
        .map(|w| w[1].clone())
        .unwrap_or_else(|| fallback.to_string())
}

struct Sample {
    context_clone_ms: f64,
    run_ms: f64,
    materialize_ms: f64,
    prepared_ms: f64,
}

struct ScenarioRun {
    id: String,
    program: Program,
    expected_rows: Option<Value>,
    rows: usize,
    compile_ms: f64,
    failures: Vec<String>,
    samples: Vec<Sample>,
}

fn ms(since: Instant) -> f64 {
    since.elapsed().as_secs_f64() * 1000.0
}

fn execute(program: &mut Program, context: &Value, isolate: bool) -> Result<(Value, Sample), SelError> {
    let clone_start = Instant::now();
    let input = if isolate { context.deep_copy(0, Pos::default())? } else { context.clone() };
    let context_clone_ms = ms(clone_start);
    let run_start = Instant::now();
    // The run gets a handle and `input` outlives the timed region, as the C++
    // runner's copy does: freeing the ~137,000 copied records is context
    // teardown (about 40 ms), not the program.
    let actual = program.run(Some(input.clone()))?;
    let run_ms = ms(run_start);
    let materialize_start = Instant::now();
    let _ = actual.dump()?;
    let materialize_ms = ms(materialize_start);
    let prepared_ms = ms(run_start);
    drop(input);
    Ok((actual, Sample { context_clone_ms, run_ms, materialize_ms, prepared_ms }))
}

fn same_rows(actual: &Value, expected: &Option<Value>) -> Result<bool, SelError> {
    match expected {
        Some(rows) => actual.eql(rows, 0, Pos::default()),
        None => Ok(false),
    }
}

fn main() {
    if let Err(error) = run() {
        eprintln!("Rust scale runner: {error}");
        std::process::exit(1);
    }
}

fn run() -> Result<(), Failure> {
    let args: Vec<String> = std::env::args().collect();
    register_benchmark_builtins()?;
    let dataset_path = arg(&args, "--dataset", "tools/scale-test/dataset-10x.json");
    let reference_path = arg(&args, "--reference", "tools/scale-test/benchmark_results.json");
    let output_path = arg(&args, "--output", "");
    let only = arg(&args, "--only", "");
    let timing_mode = arg(&args, "--timing-mode", "steady-state");
    let context_mode = arg(&args, "--context-mode", "isolated");
    let runs: usize = arg(&args, "--runs", "1").parse().unwrap_or(1);
    let warmups: usize = arg(&args, "--warmups", "0").parse().unwrap_or(0);
    if runs < 1 {
        return Err("--runs must be positive and --warmups non-negative".into());
    }
    if timing_mode != "steady-state" && timing_mode != "gc-controlled" {
        return Err(format!("unsupported timing mode: {timing_mode}").into());
    }
    if context_mode != "isolated" && context_mode != "reuse" {
        return Err(format!("unsupported context mode: {context_mode}").into());
    }

    let dataset = read_json(&dataset_path)?;
    let reference = read_json(&reference_path)?;
    let Json2::Obj(tables) = &dataset else { return Err("dataset must be an object".into()) };
    let Json2::Arr(scenarios) = &reference else { return Err("reference must be an array".into()) };
    let context = Value::none();
    for (table, rows) in tables {
        context.set(&table.to_ascii_uppercase(), rows.to_value()?, Pos::default())?;
    }
    add_distances(&context)?;
    let mut counts = RepresentationCounts::default();
    count_representation(&context, &mut counts);
    let context_signature = context.structural_hash()?;

    let mut cases = Vec::new();
    let mut reference_ids = Vec::new();
    for expected in scenarios {
        let id = expected.field("id").and_then(Json2::text).ok_or("reference scenario has no id")?.to_string();
        if !only.is_empty() && !only.split(',').any(|o| o == id) {
            continue;
        }
        // The scenarios this run covers, as the C++ runner reports them.
        reference_ids.push(id.clone());
        let query = expected.field("query").and_then(Json2::text).ok_or(format!("reference scenario has no query: {id}"))?;
        let compile_start = Instant::now();
        let program = compile(query)?;
        let compile_ms = ms(compile_start);
        let mut failures = Vec::new();
        let continuation = matches!(expected.field("has_continuation"), Some(Json2::Bool(true)));
        for dialect in ["postgresql", "mariadb"] {
            let plan = plan_hybrid(&program, dialect, Some(&schema(dialect)), Options::default());
            let key = if dialect == "postgresql" { "sql_postgres" } else { "sql_mariadb" };
            let expected_sql = expected.field(key).and_then(Json2::text);
            match (&plan.sql_statement, expected_sql) {
                (Some(sql), Some(text)) => {
                    if sql.as_statement(Mode::Inline)? != text {
                        failures.push(format!("{dialect} SQL differs from Lisp reference"));
                    }
                }
                (None, None) => {}
                _ => failures.push(format!("{dialect} SQL presence differs")),
            }
            let has_sql = expected_sql.is_some();
            if plan.is_hybrid != (has_sql && continuation) {
                failures.push(format!("{dialect} hybrid flag differs"));
            }
            if plan.pure_sql != (has_sql && !continuation) {
                failures.push(format!("{dialect} pureSql flag differs"));
            }
            if plan.continuation_program.is_some() != continuation {
                failures.push(format!("{dialect} continuation presence differs"));
            }
        }
        let rows_json = expected.field("in_memory_rows");
        cases.push(ScenarioRun {
            id,
            program,
            expected_rows: rows_json.map(Json2::to_value).transpose()?,
            rows: rows_json.map_or(0, Json2::len),
            compile_ms,
            failures,
            samples: Vec::new(),
        });
    }
    if cases.is_empty() {
        return Err("--only selected no scenarios".into());
    }

    let isolate = context_mode == "isolated";
    let mut all_passed = true;
    for item in &mut cases {
        let before = context.structural_hash()?;
        match execute(&mut item.program, &context, isolate) {
            Ok((actual, _)) => {
                if !same_rows(&actual, &item.expected_rows)? {
                    item.failures.push("in-memory rows differ from Lisp reference during validation".into());
                }
            }
            Err(error) => item.failures.push(format!("validation runtime: {error}")),
        }
        let after = context.structural_hash()?;
        if before != after || after != context_signature {
            item.failures.push("prepared context changed during untimed validation".into());
        }
        for warmup in 0..warmups {
            println!("[rust] {} warmup {}/{}", item.id, warmup + 1, warmups);
            match execute(&mut item.program, &context, isolate) {
                Ok((actual, _)) => {
                    if !same_rows(&actual, &item.expected_rows)? {
                        item.failures.push("warmup result differs from Lisp reference".into());
                    }
                }
                Err(error) => item.failures.push(format!("warmup runtime: {error}")),
            }
        }
        for run in 0..runs {
            println!("[rust] {} measured {}/{}", item.id, run + 1, runs);
            match execute(&mut item.program, &context, isolate) {
                Ok((actual, sample)) => {
                    if !same_rows(&actual, &item.expected_rows)? {
                        item.failures.push("measured result differs from Lisp reference".into());
                    }
                    if context.structural_hash()? != context_signature {
                        item.failures.push("measured run changed prepared context".into());
                    }
                    item.samples.push(sample);
                }
                Err(error) => item.failures.push(format!("measured runtime: {error}")),
            }
        }
        let passed = item.failures.is_empty() && item.samples.len() == runs;
        all_passed &= passed;
        println!("{} {}: {}", if passed { "PASS" } else { "FAIL" }, item.id,
                 if passed { "in-memory + PostgreSQL SQL + MariaDB SQL" } else { item.failures[0].as_str() });
    }

    if !output_path.is_empty() {
        let mut table_rows = serde_json::Map::new();
        let mut total_source_rows = 0;
        for (table, rows) in tables {
            table_rows.insert(table.to_ascii_uppercase(), json!(rows.len()));
            total_source_rows += rows.len();
        }
        let binary = std::env::current_exe()?.to_string_lossy().into_owned();
        let scenario_reports: Vec<Json> = cases.iter().map(|item| {
            json!({
                "id": item.id,
                "rows": item.rows,
                "compile_ms": item.compile_ms,
                "samples": item.samples.iter().map(|s| json!({
                    "context_clone_ms": s.context_clone_ms,
                    "program_run_ms": s.run_ms,
                    "materialize_ms": s.materialize_ms,
                    "prepared_total_ms": s.prepared_ms,
                    "elapsed_ms": s.prepared_ms,
                })).collect::<Vec<_>>(),
                "context_unchanged": !item.failures.iter().any(|f| f.contains("context")),
                "passed": item.failures.is_empty(),
                "failures": item.failures,
                "parity": {"passed": item.failures.is_empty(), "failures": item.failures},
            })
        }).collect();
        let report = json!({
            "schema_version": 2,
            "implementation": "rust",
            "passed": all_passed,
            "metadata": {
                "fixture": {"path": dataset_path, "sha256": sha256(&dataset_path), "table_rows": table_rows,
                            "total_source_rows": total_source_rows, "schema_version": 1},
                "reference_path": reference_path,
                "reference_sha256": sha256(&reference_path),
                "reference_scenario_ids": reference_ids,
                "runtime": {
                    "os": format!("{} {}", std::env::consts::OS, command_line("uname", "-r")),
                    "machine": std::env::consts::ARCH,
                    "cpu": cpu_model(),
                    "logical_cpus": std::thread::available_parallelism().map_or(0, |n| n.get()),
                    "available_memory_bytes": available_memory_bytes(),
                    "compiler_version": command_line("rustc", "--version"),
                    "profile": "release (lto = thin, codegen-units = 1)",
                    "binary_path": binary,
                    "binary_sha256": sha256(&binary),
                },
                "representation": {"shaped_records": counts.shaped_records,
                                   "fallback_records": counts.fallback_records, "lists": counts.lists},
                "timing_mode": timing_mode,
                "context_mode": context_mode,
                "gc_policy": "not applicable (native Rust)",
                "runs": runs,
                "warmups": warmups,
            },
            "scenarios": scenario_reports,
        });
        if let Some(dir) = Path::new(&output_path).parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir)?;
        }
        std::fs::write(&output_path, serde_json::to_string_pretty(&report)? + "\n")
            .map_err(|e| format!("cannot write {output_path}: {e}"))?;
    }
    let passed = cases.iter().filter(|c| c.failures.is_empty() && c.samples.len() == runs).count();
    println!("Rust scale parity: {}/{} passed; mode={}; context={}; runs={}; warmups={}",
             passed, cases.len(), timing_mode, context_mode, runs, warmups);
    if all_passed { Ok(()) } else { Err("parity failed".into()) }
}
