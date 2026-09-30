#![cfg(feature = "sql")]
use sel_lang::{
    compile,
    sql::{translate, Binding, Bindings, Options},
    Value,
};
use std::collections::HashMap;

#[test]
fn nul_text_is_refused_before_selecting_a_render_mode() {
    let value = Value::text_owned("a\0b".into());
    let bindings = Bindings::new(Some(HashMap::from([
        ("X".into(), Binding::value(value.clone(), None)),
        ("XS".into(), Binding::value(Value::list(vec![value]), None)),
    ])));
    for dialect in ["mariadb", "postgresql", "sqlite"] {
        for source in ["\"a\\u{0}b\"", "X", "XS[1]"] {
            let program = compile(source).unwrap();
            let error =
                translate(&program, dialect, Some(&bindings), Options::default()).unwrap_err();
            assert_eq!(error.code, "E_SQL_UNSUPPORTED", "{dialect}: {source}");
        }
    }
}

#[test]
fn arithmetic_text_constants_become_exact_numbers_without_stale_slots() {
    use sel_lang::sql::{Mode, SqlKind};
    for dialect in ["mariadb", "postgresql", "sqlite"] {
        let program = compile("(REPLACE(\"00\", \"\", \"0041\") + 1) & \" tail\"").unwrap();
        let fragment = translate(&program, dialect, None, Options::default()).unwrap();
        assert_eq!(fragment.params.len(), 3, "{dialect}");
        assert_eq!(
            fragment.param_kinds,
            vec![SqlKind::Num, SqlKind::Num, SqlKind::Text]
        );
        assert_eq!(
            fragment
                .params
                .iter()
                .map(Value::scalar)
                .collect::<Vec<_>>(),
            vec!["41", "1", " tail"]
        );
        assert_eq!(
            fragment
                .bindings()
                .iter()
                .map(Value::scalar)
                .collect::<Vec<_>>(),
            vec![" tail"]
        );
        assert!(!fragment
            .as_value(Mode::Params)
            .unwrap()
            .to_uppercase()
            .contains("REPLACE"));

        let fragment = translate(
            &compile("\"1.10\" + 2").unwrap(),
            dialect,
            None,
            Options::default(),
        )
        .unwrap();
        assert_eq!(fragment.params[0].scalar(), "1.10");
        assert_eq!(fragment.param_kinds[0], SqlKind::Num);
    }
}
