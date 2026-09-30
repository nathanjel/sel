use sel_lang::{compile, register_function, Value};

#[test]
fn out_of_range_host_argument_readers_return_sel_errors() {
    for (index, reader) in [
        "value",
        "text",
        "bytes",
        "bool",
        "decimal",
        "integer",
        "nonnegative",
        "symbol",
        "node",
        "position",
        "is_symbol",
    ]
    .into_iter()
    .enumerate()
    {
        let name = format!("OOB_READER_{index}");
        register_function(&name, 1, 1, move |args| {
            let i = usize::MAX;
            match reader {
                "value" => {
                    args.val(i)?;
                }
                "text" => {
                    args.text(i)?;
                }
                "bytes" => {
                    args.bytes(i)?;
                }
                "bool" => {
                    args.bool(i)?;
                }
                "decimal" => {
                    args.dec(i)?;
                }
                "integer" => {
                    args.int(i)?;
                }
                "nonnegative" => {
                    args.non_neg_int(i)?;
                }
                "symbol" => {
                    args.symbol(i)?;
                }
                "node" => {
                    args.node(i)?;
                }
                "position" => {
                    args.pos_of(i)?;
                }
                "is_symbol" => {
                    args.is_symbol(i)?;
                }
                _ => unreachable!(),
            }
            Ok(Value::none())
        })
        .unwrap();
        let mut program = compile(&format!("{name}(1)")).unwrap();
        for _ in 0..2 {
            let error = program.run(None).unwrap_err();
            assert_eq!(error.code, "E_BAD_ARG", "{reader}");
            assert_eq!((error.pos.line, error.pos.col), (1, 1));
        }
    }
}
