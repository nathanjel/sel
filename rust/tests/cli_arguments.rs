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
