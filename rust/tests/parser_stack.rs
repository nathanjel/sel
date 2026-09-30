use sel_lang::compile;

// Compiling a program nested past MAX_DEPTH must answer E_DEPTH, never overflow
// the host's stack: 256 KiB in a release build, and the Rust default of 2 MiB
// for a thread in a debug build (unoptimised frames are several times larger).
fn stack() -> usize {
    if cfg!(debug_assertions) { 2 * 1024 * 1024 } else { 256 * 1024 }
}

fn compile_on_small_stack(source: String) -> (String, usize, usize) {
    std::thread::Builder::new()
        .stack_size(stack())
        .spawn(move || {
            let error = compile(&source).unwrap_err();
            (error.code.to_string(), error.pos.line, error.pos.col)
        })
        .unwrap()
        .join()
        .unwrap()
}

#[test]
fn deep_programs_reach_the_depth_limit_on_bounded_stacks() {
    let n = 250;
    for (source, col) in [
        (format!("{}1{}", "(".repeat(n), ")".repeat(n)), 101),
        (format!("{}1{}", "\"{".repeat(n), "}\"".repeat(n)), 101),
        (format!("{}1", "-".repeat(n)), 200),
        (format!("{}1{}", "LEN(".repeat(n), ")".repeat(n)), 401),
    ] {
        let preview = source[..12].to_string();
        let (code, line, column) = compile_on_small_stack(source);
        assert_eq!((code.as_str(), line, column), ("E_DEPTH", 1, col), "{preview}");
    }
}
