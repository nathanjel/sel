#![cfg(unix)]

use std::ffi::OsString;
use std::os::unix::ffi::OsStringExt;
use std::process::Command;

#[test]
fn invalid_utf8_expression_arguments_are_language_errors() {
    for deps in [false, true] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_sel"));
        if deps {
            command.arg("--deps");
        }
        let output = command
            .arg("-e")
            .arg(OsString::from_vec(vec![b'"', 0xc5, 0x82, 0xff, b'"']))
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(1));
        assert!(output.stdout.is_empty());
        let stderr = String::from_utf8(output.stderr).unwrap();
        assert!(stderr.starts_with("E_UTF8 at line 1 column 3:"), "{stderr}");
        assert_eq!(stderr.lines().count(), 1);
    }
}

#[test]
fn valid_source_can_be_read_from_a_non_utf8_path() {
    let mut name = format!("sel-cli-{}-", std::process::id()).into_bytes();
    name.extend_from_slice(&[0xff, b'.', b's', b'e', b'l']);
    let path = std::env::temp_dir().join(OsString::from_vec(name));
    std::fs::write(&path, b"21 * 2").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_sel")).arg(&path).output();
    std::fs::remove_file(&path).unwrap();
    let output = output.unwrap();
    assert!(output.status.success());
    assert_eq!(output.stdout, b"42\n");
    assert!(output.stderr.is_empty());
}

/// Runs `sel` with `args` and `stdin` (a pipe, never a terminal): exit code,
/// stdout, stderr.
fn sel(args: &[&str], stdin: &[u8]) -> (Option<i32>, String, String) {
    use std::io::Write;
    use std::process::Stdio;
    let mut child = Command::new(env!("CARGO_BIN_EXE_sel"))
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin).unwrap();
    let out = child.wait_with_output().unwrap();
    (out.status.code(), String::from_utf8(out.stdout).unwrap(), String::from_utf8(out.stderr).unwrap())
}

// The CLI contract every host's `sel` keeps (docs/usage/repl.md).
#[test]
fn usage_errors_exit_2_with_a_sel_message() {
    let (code, out, err) = sel(&["-e"], b"");
    assert_eq!((code, out.as_str(), err.as_str()), (Some(2), "", "sel: -e needs an expression\n"));
    let (code, out, err) = sel(&["--frobnicate"], b"");
    assert_eq!((code, out.as_str(), err.as_str()), (Some(2), "", "sel: unknown option --frobnicate\n"));
    let (code, out, err) = sel(&["-e", "1", "extra"], b"");
    assert_eq!((code, out.as_str(), err.as_str()), (Some(2), "", "sel: unexpected argument extra\n"));
    // A second source names what it would have been: here the expression.
    let (code, _, err) = sel(&["-e", "1", "-e", "1+1"], b"");
    assert_eq!((code, err.as_str()), (Some(2), "sel: unexpected argument 1+1\n"));
}

#[test]
fn help_and_version_go_to_stdout_and_exit_0() {
    for flag in ["--help", "-h"] {
        let (code, out, err) = sel(&[flag], b"");
        assert_eq!(code, Some(0));
        assert!(out.starts_with("usage: sel -e EXPR"), "{out}");
        assert!(err.is_empty());
    }
    let (code, out, err) = sel(&["--version"], b"");
    assert_eq!((code, out, err.as_str()), (Some(0), format!("sel {}\n", env!("CARGO_PKG_VERSION")), ""));
}

#[test]
fn unreadable_files_exit_1_naming_the_path() {
    let missing = std::env::temp_dir().join(format!("sel-cli-missing-{}.sel", std::process::id()));
    for path in [missing.to_str().unwrap(), std::env::temp_dir().to_str().unwrap()] {
        let (code, out, err) = sel(&[path], b"");
        assert_eq!(code, Some(1));
        assert!(out.is_empty());
        assert!(err.starts_with(&format!("sel: cannot read {path}")), "{err}");
    }
}

#[test]
fn evaluation_errors_and_dependencies() {
    let (code, out, err) = sel(&["-e", "1 +"], b"");
    assert_eq!(code, Some(1));
    assert!(out.is_empty());
    assert!(err.starts_with("E_SYNTAX at line 1 column "), "{err}");
    // An empty dependency list prints nothing at all.
    assert_eq!(sel(&["--deps", "-e", "1"], b""), (Some(0), String::new(), String::new()));
    assert_eq!(sel(&["-e", "B + A", "--deps"], b""), (Some(0), "A\nB\n".into(), String::new()));
}

#[test]
fn the_repl_on_a_pipe_writes_only_results_and_errors() {
    // No prompt and no closing newline on a pipe; a line of SEL whitespace is
    // skipped, one of NBSP is a program (E_SYNTAX, on stderr).
    let (code, out, err) = sel(&[], "A = 1\n \t\r\nA + 1\n\u{A0}\n".as_bytes());
    assert_eq!(code, Some(0));
    assert_eq!(out, "1\n2\n");
    assert!(err.starts_with("E_SYNTAX at line 1 column 1:"), "{err}");
    assert_eq!(err.lines().count(), 1);
}
