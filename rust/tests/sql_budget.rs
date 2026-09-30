#![cfg(feature = "sql")]
use sel_lang::{compile, sql::{Binding, Bindings, Options, SqlKind, Mode, translate}};
use std::collections::HashMap;

fn sized(nodes: usize) -> String {
    let bits: Vec<_> = (0..18).filter(|i| ((nodes - 1) / 2) & (1 << i) != 0).collect();
    let mut source = String::from("X0 = N; ");
    for i in 1..=*bits.last().unwrap() {
        source.push_str(&format!("X{i} = X{} + X{}; ", i-1, i-1));
    }
    source.push_str(&bits.iter().map(|i| format!("X{i}")).collect::<Vec<_>>().join(" + "));
    source.push_str(" > 0");
    source
}

#[test]
fn rendered_sql_obeys_size_boundary() {
    let mut bindings = HashMap::new();
    bindings.insert("N".into(), Binding::column("n", "t", SqlKind::Num, false, false, false, "", "", false));
    let bindings = Bindings::new(Some(bindings));
    let accepted = compile(&sized(200_001)).unwrap();
    let result = translate(&accepted, "mariadb", Some(&bindings), Options::default()).unwrap();
    assert!(result.as_condition(Mode::Inline).unwrap().len() > 200_000);
    let refused = compile(&sized(300_001)).unwrap();
    let error = translate(&refused, "mariadb", Some(&bindings), Options::default()).unwrap_err();
    assert_eq!(error.code, "E_SQL_SIZE");
}
