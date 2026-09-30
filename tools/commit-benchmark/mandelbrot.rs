// mandelbrot.cpp transcribed: compile examples/mandelbrot.sel once, then time
// run() + as_text() per sample (MANDEL_WARMUPS, default 2; MANDEL_RUNS, default
// 5) and write {compile_ms, samples_ms, outputs, warmups, runs} to argv[1].
// Built as the crate's `mandelbrot` example (rust/Cargo.toml); run from the
// repository root.

use sel_lang::{compile, Pos};
use std::time::Instant;

fn count(name: &str, fallback: usize) -> usize {
    std::env::var(name).ok().and_then(|v| v.parse().ok()).unwrap_or(fallback)
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let output = std::env::args().nth(1).ok_or("usage: mandelbrot OUTPUT.json")?;
    let source = std::fs::read_to_string("examples/mandelbrot.sel")?;
    let started = Instant::now();
    let mut program = compile(&source)?;
    let compile_ms = started.elapsed().as_secs_f64() * 1000.0;
    let warmups = count("MANDEL_WARMUPS", 2);
    let runs = count("MANDEL_RUNS", 5);
    let mut samples = Vec::new();
    let mut outputs = Vec::new();
    for i in 0..warmups + runs {
        let started = Instant::now();
        let text = program.run(None)?.as_text(Pos::default())?;
        let ms = started.elapsed().as_secs_f64() * 1000.0;
        if i >= warmups {
            samples.push(ms);
            outputs.push(text);
        }
    }
    let report = serde_json::json!({"compile_ms": compile_ms, "samples_ms": samples,
        "outputs": outputs, "warmups": warmups, "runs": runs});
    std::fs::write(output, report.to_string())?;
    Ok(())
}
