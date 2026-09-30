//! Scenario 1 prepared-context benchmark. JSON loading, compilation, validation,
//! and context hashing are outside the reported execution/materialization time.
use sel_lang::{compile, Entry, Value};
use serde_json::{json, Value as Json};
use std::{fs, time::Instant};

/// The fixture as a SEL value, numbers as text. Read straight from the parser,
/// not through serde_json::Value, which sorts object keys: a SEL record keeps
/// its keys in the order the file gives them.
struct Fixture(Value);

impl<'de> serde::Deserialize<'de> for Fixture {
    fn deserialize<D: serde::Deserializer<'de>>(d: D) -> Result<Self, D::Error> {
        struct V;
        impl<'de> serde::de::Visitor<'de> for V {
            type Value = Fixture;
            fn expecting(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { f.write_str("JSON") }
            fn visit_unit<E>(self) -> Result<Fixture, E> { Ok(Fixture(Value::null())) }
            fn visit_bool<E>(self, b: bool) -> Result<Fixture, E> { Ok(Fixture(Value::bool(b))) }
            fn visit_i64<E>(self, n: i64) -> Result<Fixture, E> { Ok(Fixture(Value::text_owned(n.to_string()))) }
            fn visit_u64<E>(self, n: u64) -> Result<Fixture, E> { Ok(Fixture(Value::text_owned(n.to_string()))) }
            fn visit_f64<E>(self, n: f64) -> Result<Fixture, E> { Ok(Fixture(Value::text_owned(n.to_string()))) }
            fn visit_str<E>(self, s: &str) -> Result<Fixture, E> { Ok(Fixture(Value::text_owned(s.to_string()))) }
            fn visit_seq<A: serde::de::SeqAccess<'de>>(self, mut a: A) -> Result<Fixture, A::Error> {
                let mut xs = Vec::new();
                while let Some(Fixture(x)) = a.next_element()? { xs.push(x); }
                Ok(Fixture(Value::list(xs)))
            }
            fn visit_map<A: serde::de::MapAccess<'de>>(self, mut a: A) -> Result<Fixture, A::Error> {
                let mut entries = Vec::new();
                while let Some((key, Fixture(val))) = a.next_entry::<String, Fixture>()? {
                    entries.push(Entry { key, val });
                }
                Ok(Fixture(Value::record_from_entries(entries)))
            }
        }
        d.deserialize_any(V)
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() != 4 { return Err("usage: scale_bench DATASET REFERENCE RUNS WARMUPS".into()); }
    let runs: usize = args[2].parse()?;
    let warmups: usize = args[3].parse()?;
    if runs == 0 { return Err("RUNS must be positive".into()); }
    let Fixture(dataset) = serde_json::from_slice(&fs::read(&args[0])?)?;
    let reference: Json = serde_json::from_slice(&fs::read(&args[1])?)?;
    let scenario = reference.as_array().ok_or("reference must be an array")?.iter()
        .find(|v| v["id"] == "scenario1").ok_or("scenario1 missing")?;
    let query = scenario["query"].as_str().ok_or("query missing")?;
    let context = Value::record_from_entries(dataset.entries().into_iter()
        .map(|e| Entry { key: e.key.to_ascii_uppercase(), val: e.val }).collect());
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
