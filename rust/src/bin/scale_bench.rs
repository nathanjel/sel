//! Scenario 1 prepared-context benchmark. JSON loading, compilation, validation,
//! and context hashing are outside the reported execution/materialization time.
use sel_lang::{compile, Entry, Value};
use serde_json::{json, Value as Json};
use std::{fs, time::Instant};

fn value(v: &Json) -> Value {
    match v {
        Json::Null => Value::null(),
        Json::Bool(b) => Value::bool(*b),
        Json::Number(n) => Value::text_owned(n.to_string()),
        Json::String(s) => Value::text_owned(s.clone()),
        Json::Array(xs) => Value::list(xs.iter().map(value).collect()),
        Json::Object(xs) => Value::record_from_entries(xs.iter().map(|(k, v)| Entry { key: k.clone(), val: value(v) }).collect()),
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 { return Err("usage: scale_bench DATASET REFERENCE RUNS WARMUPS".into()); }
    let runs: usize = args[2].parse()?;
    let warmups: usize = args[3].parse()?;
    if runs == 0 { return Err("RUNS must be positive".into()); }
    let dataset: Json = serde_json::from_slice(&fs::read(&args[0])?)?;
    let reference: Json = serde_json::from_slice(&fs::read(&args[1])?)?;
    let scenario = reference.as_array().ok_or("reference must be an array")?.iter()
        .find(|v| v["id"] == "scenario1").ok_or("scenario1 missing")?;
    let query = scenario["query"].as_str().ok_or("query missing")?;
    let context = Value::record_from_entries(dataset.as_object().ok_or("dataset must be an object")?.iter()
        .map(|(k, v)| Entry { key: k.to_ascii_uppercase(), val: value(v) }).collect());
    let original_hash = context.structural_hash()?;
    let compile_start = Instant::now();
    let mut program = compile(query)?;
    let compile_ms = compile_start.elapsed().as_secs_f64() * 1000.0;
    let mut samples = Vec::new();
    let mut expected_dump = None;
    let mut rows = Json::Null;
    for i in 0..1 + warmups + runs {
        let started = Instant::now();
        let actual = program.run(Some(context.clone()))?;
        let run_ms = started.elapsed().as_secs_f64() * 1000.0;
        let dump = actual.dump()?;
        let prepared_ms = started.elapsed().as_secs_f64() * 1000.0;
        if i == 0 {
            expected_dump = Some(dump.clone());
            rows = Json::Array(actual.entries().iter().map(|row| {
                Json::Object(row.val.entries().iter().map(|e| (e.key.clone(), Json::String(e.val.scalar()))).collect())
            }).collect());
        }
        if expected_dump.as_ref() != Some(&dump) { return Err("result changed between runs".into()); }
        if context.structural_hash()? != original_hash { return Err("prepared context was mutated".into()); }
        if i > warmups {
            samples.push(json!({"program_run_ms":run_ms,"materialize_ms":prepared_ms-run_ms,"prepared_total_ms":prepared_ms}));
        }
    }
    println!("{}", json!({"scenario":"scenario1", "dataset":args[0], "compile_ms":compile_ms,
        "context_mode":"shared-readonly", "warmups":warmups, "runs":runs,
        "samples":samples, "rows":rows}));
    Ok(())
}
