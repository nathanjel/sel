#![cfg(feature = "sql")]
use sel_lang::{compile, sql::{translate, Binding, Bindings, Mode, Options, SqlKind}};
use std::collections::HashMap;

#[test]
fn helper_free_names_survive_nested_binder_shadowing() {
    let bindings = Bindings::new(Some(HashMap::from([("A".into(), Binding::column(
        "a", "t", SqlKind::Num, false, false, false, "", "", false,
    ))])));
    for (source, control) in [
        ("X = A + 1; ALL((5,6), A, ALL((7,8), A, A > X))",
         "X = A + 1; ALL((5,6), B, ALL((7,8), C, C > X))"),
        ("X = A; ALL((5,6), A, ALL((7,8), B, A + B > X))",
         "X = A; ALL((5,6), C, ALL((7,8), B, C + B > X))"),
    ] {
        let render = |s: &str| translate(&compile(s).unwrap(), "mariadb", Some(&bindings), Options::default())
            .unwrap().as_condition(Mode::Inline).unwrap();
        assert_eq!(render(source), render(control));
    }
}

// A kind-mismatch refusal names both kinds as they are (it used to call a
// BIN-against-TEXT comparison "a BOOL with a BIN").
#[test]
fn comparison_refusals_name_both_kinds() {
    let column = |name: &str, kind| Binding::column(name, "t", kind, false, false, false, "", "", false);
    let bindings = Bindings::new(Some(std::collections::HashMap::from([
        ("B".to_string(), column("b", SqlKind::Bin)),
        ("T".to_string(), column("t", SqlKind::Text)),
    ])));
    let err = translate(&compile("B EQL T").unwrap(), "postgresql", Some(&bindings), Options::default()).unwrap_err();
    assert_eq!(err.code, "E_SQL_SHAPE");
    assert!(err.message.starts_with("EQL compares a BIN with a TEXT, which SEL answers FALSE"), "{}", err.message);
}
