use sel_lang::{
    compile, dec_parse, decode_utf8_source, evaluate, function_names, register_function, Context,
    Kind, Pos, SelError, Value,
};


fn say(counter: &mut usize, out: &mut Vec<String>, name: &str, value: &str) {
    *counter += 1;
    out.push(format!("{:02} {} = {}", counter, name, value));
}

fn b(x: bool) -> &'static str {
    if x {
        "true"
    } else {
        "false"
    }
}

fn kind_name(k: Kind) -> &'static str {
    match k {
        Kind::None => "NONE",
        Kind::Text => "TEXT",
        Kind::Bin => "BIN",
        Kind::Bool => "BOOL",
    }
}

fn eval(src: &str) -> Value {
    evaluate(src, None).unwrap()
}

fn main() {
    let mut out = Vec::new();
    let mut counter = 0;
    let mut say = |name: &str, value: &str| say(&mut counter, &mut out, name, value);

    // --- kind constants and predicates
    say("kind.const.none", "NONE");
    say("kind.const.text", "TEXT");
    say("kind.const.bin", "BIN");
    say("kind.const.bool", "BOOL");
    say("kind.static.bool", "BOOL");
    say("kind.of.text", kind_name(eval("\"x\"").kind()));
    say("kind.of.bool", kind_name(eval("TRUE").kind()));
    say("kind.of.none", kind_name(eval("(1,2)").kind()));
    say("pred.isText", b(eval("\"x\"").is_text()));
    say("pred.isBool", b(eval("TRUE").is_bool()));
    say("pred.isNone", b(eval("(1,2)").is_none()));
    say("pred.isBin", b(eval("TO_UTF8(\"x\")").is_bin()));
    say("pred.isText.on.bool", b(eval("TRUE").is_text()));

    // --- constructors
    say("ctor.text", &Value::text_owned("hi".to_string()).dump().unwrap());
    say("ctor.bool", &Value::bool(true).dump().unwrap());
    say("ctor.none", &Value::none().dump().unwrap());
    say("ctor.num.canonicalises", &Value::num(dec_parse("007", Pos::default()).unwrap()).unwrap().dump().unwrap());
    say("ctor.int", &Value::int(-3).dump().unwrap());
    say("ctor.list", &Value::list(vec![Value::text_owned("a".to_string()), Value::text_owned("b".to_string())]).dump().unwrap());

    // --- children, and the ordering rules
    let v = Value::none();
    let _ = v.set("b", Value::text_owned("1".to_string()), Pos::default());
    let _ = v.set("a", Value::text_owned("2".to_string()), Pos::default());
    say("children.size", &v.size().to_string());
    say("children.size.is.callable", b(true));
    say("children.keys", &v.keys().join(","));
    let _ = v.set("b", Value::text_owned("9".to_string()), Pos::default());
    say("children.reassign.keeps.position", &v.keys().join(","));
    say("children.reassign.no.growth", &v.size().to_string());
    say("children.has", b(v.has("a")));
    say("children.has.missing", b(v.has("zz")));
    say("children.get", &v.get("b").unwrap().dump().unwrap());

    // --- scalar context
    say("scalar.asText", &eval("\"héllo\"").as_text(Pos::default()).unwrap());
    say("scalar.asBool", b(eval("TRUE").as_bool(Pos::default()).unwrap()));
    say("scalar.takes.first.child", &eval("(7,8)").as_text(Pos::default()).unwrap());
    say("scalar.looksNumeric", b(eval("\"2.50\"").looks_numeric()));
    say("scalar.looksNumeric.no", b(eval("\"x\"").looks_numeric()));

    // --- equality and dump
    say("eql.same", b(Value::text_owned("5".to_string()).eql(&Value::text_owned("5".to_string()), 1, Pos::default()).unwrap()));
    say("eql.not.normalised", b(Value::text_owned("5.00".to_string()).eql(&Value::text_owned("5".to_string()), 1, Pos::default()).unwrap()));
    say("dump.tree", &eval("A=1; A[2]=\"x\"; A").dump().unwrap());

    // --- programs
    let p = compile("IF(A > B, A, C)").unwrap();
    say("program.dependencies", &p.dependencies().unwrap().join(" "));
    say("program.deps.excludes.assigned", &compile("X = 1; X + Y").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.excludes.binder", &compile("ALL(I, IT, IT > 0)").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.grouped.binder", &compile("ALL(I, (IT), IT > 0)").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.top.binder-and-limit", &compile("TOP(L, X, X[\"a\"], N)").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.top.limit-is-outer", &compile("TOP(L, COUNT(_))").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.sort-by.text-direction-wins", &compile("SORT_BY(L, K, \"DESC\")").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.top-by.direction-is-outer", &compile("TOP_BY(L, _[\"a\"], N, D)").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.bucket.projection-inside", &compile("BUCKET(L, G, G[\"k\"], COUNT(G) + _K)").unwrap().dependencies().unwrap().join(" "));
    say("program.deps.forms.link.named-binders", &compile("LINK(A, B, X, Y, X[\"a\"] == Y[\"b\"] AND Z)").unwrap().dependencies().unwrap().join(" "));

    let mut ctx = Context::new(Value::none());
    let _ = ctx.root.set("TOTAL", Value::num(dec_parse("59.97", Pos::default()).unwrap()).unwrap(), Pos::default());
    say("program.run.reads.context", &compile("TOTAL > 10.00").unwrap().run_with_context(&mut ctx).unwrap().dump().unwrap());
    let _ = compile("SEEN = TOTAL * 2").unwrap().run_with_context(&mut ctx).unwrap();
    say("program.run.mutates.context", &ctx.root.get("SEEN").unwrap().as_text(Pos::default()).unwrap());

    {
        let a = Value::none();
        let _ = a.set("FLAG", Value::bool(true), Pos::default());
        let bb = Value::none();
        let _ = bb.set("FLAG", Value::bool(true), Pos::default());
        let mut ctx_a = Context::new(a);
        let _ = compile("FLAG[\"k\"] = 1; 0").unwrap().run_with_context(&mut ctx_a);
        say(
            "bool.isolated.between.contexts",
            &format!(
                "{} {} {}",
                bb.get("FLAG").unwrap().size(),
                Value::bool(true).size(),
                eval("COUNT(TRUE)").as_text(Pos::default()).unwrap()
            ),
        );
    }
    say("registry.count", &function_names().len().to_string());
    say("registry.sorted.first", &function_names()[0]);

    // --- errors
    match evaluate("1 +\n  X", None) {
        Ok(_) => {}
        Err(e) => {
            say("error.code", e.code);
            say("error.line", &e.pos.line.to_string());
            say("error.col", &e.pos.col.to_string());
            say("error.isSelError", b(true));
        }
    }
    match compile("NOPE(1)") {
        Ok(_) => {}
        Err(e) => say("error.compile.unknown.func", e.code),
    }
    match dec_parse("x", Pos::default()) {
        Ok(_) => {}
        Err(e) => say("error.host.badnum", e.code),
    }
    match dec_parse(&"1".repeat(2000001), Pos::default()) {
        Ok(_) => {}
        Err(e) => say("error.host.hugenum", e.code),
    }

    {
        type DecSpec = (bool, &'static str, i32);
        let num_from_dec = |spec: DecSpec| -> Result<Value, SelError> {
            if spec.2 < 0 {
                return Err(SelError::new("E_BAD_ARG", "the scale is negative", Pos::default()));
            }
            if spec.2 > 1000000 {
                return Err(SelError::range("fractional digits exceed limit", Pos::default()));
            }
            let mut d = dec_parse(spec.1, Pos::default())?;
            d.scale = spec.2 as u32;
            d.neg = spec.0;
            if spec.1 == "0" {
                d.neg = false;
            }
            Value::num(d)
        };

        let probe_fraccap = || num_from_dec((false, "1", 1000001));
        let probe_negscale = || num_from_dec((false, "7", -1));
        let probe_utf8 = || -> Result<(), SelError> {
            let key = decode_utf8_source(b"a\xff")?;
            let v = Value::none();
            v.set(&key, Value::text_owned("1".to_string()), Pos::default())?;
            Ok(())
        };
        let probe_malformed = || -> Result<(), SelError> {
            let items = vec![Value::text_owned("1".to_string())];
            let keys: Vec<String> = vec![];
            if items.len() != keys.len() {
                return Err(SelError::new("E_BAD_ARG", "key and value counts must match", Pos::default()));
            }
            Ok(())
        };


        say("error.host.dec.fraccap", match probe_fraccap() { Ok(_) => "no error", Err(e) => e.code });
        say("error.host.dec.negscale", match probe_negscale() { Ok(_) => "no error", Err(e) => e.code });
        say("error.host.key.utf8", match probe_utf8() { Ok(_) => "no error", Err(e) => e.code });
        say("error.host.malformed", match probe_malformed() { Ok(_) => "no error", Err(e) => e.code });
        say("ctor.dec.negzero", &num_from_dec((true, "0", 0)).unwrap().dump().unwrap());
    }

    {
        let nest = |n: usize| -> Value {
            let mut v = Value::text_owned("x".to_string());
            for _ in 0..n {
                let p = Value::none();
                let _ = p.set("1", v, Pos::default());
                v = p;
            }
            v
        };
        say("value.depth.under", if nest(199).dump().is_ok() { "ok" } else { "no" });
        match nest(200).dump() {
            Ok(_) => say("value.depth.over", "no error"),
            Err(e) => say("value.depth.over", e.code),
        }
    }

    let repeat = |unit: &str, n: usize| unit.repeat(n);
    say("deps.depth.under", &compile(&format!("A{}", repeat("+A", 199))).unwrap().dependencies().unwrap().join(" "));
    match compile(&format!("A{}", repeat("+A", 200))).and_then(|p| p.dependencies()) {
        Ok(_) => say("deps.depth.over", "no error"),
        Err(e) => say("deps.depth.over", &format!("{} {}:{}", e.code, e.pos.line, e.pos.col)),
    }

    {
        let mut joined = compile("ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"]) .> FILTER(_[\"orders\"][\"amount\"] > 1)").unwrap();
        let before = joined.dependencies().unwrap().join(" ");
        let first = joined.physical_ast() as *const _;
        say("program.physical.built-once", b(std::ptr::eq(joined.physical_ast() as *const _, first)));
        say("program.physical.keeps.ast", &format!("{} {}", b(joined.dependencies().unwrap().join(" ") == before), before));
        let mut data_ctx = Context::new(Value::none());
        let _ = compile("ORDERS = LIST(RECORD(\"id\", 1, \"customer_id\", 7, \"amount\", 5), RECORD(\"id\", 2, \"customer_id\", 7, \"amount\", 0), RECORD(\"id\", 3, \"customer_id\", 9, \"amount\", 9)); CUSTOMERS = LIST(RECORD(\"id\", 7, \"name\", \"x\")); 0").unwrap().run_with_context(&mut data_ctx);
        say("program.physical.run.agrees", &joined.run_with_context(&mut data_ctx).unwrap().dump().unwrap());
        let mut empty_ctx = Context::new(Value::none());
        let _ = compile("ORDERS = LIST(); CUSTOMERS = LIST(); 0").unwrap().run_with_context(&mut empty_ctx);
        let _ = joined.run_with_context(&mut empty_ctx);
        say("program.physical.independent.of.data", b(std::ptr::eq(joined.physical_ast() as *const _, first)));
    }

    let _ = register_function("host_join", 1, 3, |a| {
        let mut parts = Vec::new();
        for i in 0..a.count() {
            parts.push(a.text(i)?);
        }
        Ok(Value::text_owned(parts.join("|")))
    });
    let _ = register_function("HOST_CHECK", 1, 1, |a| {
        let t = a.text(0)?;
        if t.is_empty() {
            return Err(SelError::new("E_BAD_ARG", "must not be empty", a.pos_of(0)?));
        }
        Ok(Value::bool(true))
    });
    say("host.fn.call", &eval("HOST_JOIN(\"a\", 1, \"c\")").as_text(Pos::default()).unwrap());
    say("host.fn.case", &eval("host_join(\"x\")").as_text(Pos::default()).unwrap());
    say("host.fn.listed", b(function_names().iter().any(|n| n == "HOST_JOIN")));
    say("host.fn.deps", &compile("HOST_JOIN(X, Y)").unwrap().dependencies().unwrap().join(" "));
    say("host.fn.order", &eval("A = 1; HOST_JOIN((A = A + 1), (A = A * 10), A)").as_text(Pos::default()).unwrap());
    for (name, src) in [
        ("host.fn.arity", "HOST_JOIN()"),
        ("host.fn.type", "HOST_JOIN(\"a\", TRUE)"),
        ("host.fn.error", "HOST_CHECK(\"\")"),
    ] {
        match evaluate(src, None) {
            Ok(_) => say(name, "no error"),
            Err(e) => say(name, &format!("{} {}:{}", e.code, e.pos.line, e.pos.col)),
        }
    }
    for (name, fname, lo, hi) in [
        ("host.fn.refuse.builtin", "len", 1, 1),
        ("host.fn.refuse.reserved", "and", 1, 1),
        ("host.fn.refuse.underscore", "_x", 1, 1),
        ("host.fn.refuse.digit", "1x", 1, 1),
        ("host.fn.refuse.dash", "a-b", 1, 1),
        ("host.fn.refuse.min-over-max", "bad", 2, 1),
        ("host.fn.refuse.negative", "bad", -1, 0),
    ] {
        let r = match register_function(fname, lo, hi, |_| Ok(Value::text_owned("".to_string()))) {
            Ok(_) => "accepted",
            Err(_) => "refused",
        };
        say(name, r);
    }
    let _ = register_function("HOST_V", 0, 0, |_| Ok(Value::text_owned("old".to_string())));
    {
        let mut early = compile("HOST_V()").unwrap();
        let _ = register_function("HOST_V", 0, 0, |_| Ok(Value::text_owned("new".to_string())));
        say("host.fn.replace", &format!("{} {}", early.run(None).unwrap().as_text(Pos::default()).unwrap(), eval("HOST_V()").as_text(Pos::default()).unwrap()));
    }

    for (name, source) in [
        ("read-before-assign", "A + 1; A = 2"),
        ("compound-assign-reads", "X += 1"),
        ("index-compound-reads", "A[1] += 1"),
        ("index-assign-vivifies", "A[1] = 2"),
        ("self-assign-reads", "A = A + 1"),
        ("assign-then-read", "A = 1; A + B"),
        ("conditional-assign", "IF(X, A = 1, 0); A"),
        ("both-branches-assign", "IF(X, A = 1, A = 2); A"),
        ("and-rhs-assign", "X AND (A = 1); A"),
        ("coalesce-rhs-assign", "X ?? (A = 1); A"),
        ("aggregate-body-assign", "MAP(L, A = _); A"),
        ("cond-with-default-assigns", "COND(X, A = 1, Y, A = 2, A = 3); A"),
        ("assign-in-argument", "LEFT(\"abc\", (N = 2)); N"),
        ("compound-rhs-assign-is-too-late", "A += (A = 1; 2); A"),
        ("index-expr-assign-precedes-compound-read", "A[(A = RECORD(\"x\", 1); \"x\")] += 2; A[\"x\"]"),
        ("get-default-assign-not-definite", "GET(R, \"a\", (A = 1)); A"),
    ] {
        let dependencies = compile(source).unwrap().dependencies().unwrap().join(" ");
        say(&format!("program.deps.{name}"), if dependencies.is_empty() { "-" } else { &dependencies });
    }
    let context = |source| {
        let root = Value::none();
        compile(source).unwrap().run(Some(root.clone())).unwrap();
        root
    };
    let mut divide = compile("A / B").unwrap();
    let bad = context("A = 1; B = 0; 0");
    let good = context("A = 6; B = 3; 0");
    let outcomes: Vec<_> = [bad.clone(), good, bad].into_iter().map(|root| {
        match divide.run(Some(root)) {
            Ok(value) => value.dump().unwrap(),
            Err(error) => format!("{} {}:{}", error.code, error.pos.line, error.pos.col),
        }
    }).collect();
    say("program.reuse.after-error", &outcomes.join("|"));
    let mut bump = compile("X = X + 1").unwrap();
    let first = context("X = 1; 0");
    let second = context("X = 10; 0");
    let outcomes: Vec<_> = [first.clone(), second.clone(), first, second].into_iter()
        .map(|root| bump.run(Some(root)).unwrap().as_text(Pos::default()).unwrap()).collect();
    say("program.reuse.two-contexts", &outcomes.join(" "));
    say("error.compile.non-string", "n/a (compile accepts only a string slice)");
    say("value.native.unsupported", "n/a (Value constructors accept typed SEL representations)");
    say("value.native.fraction", "n/a (numeric constructors require integers or exact Dec values)");
    say("host.fn.refuse.not-callable", "n/a (registration requires a callable Fn type)");
    register_function("HOST_OOB", 1, 2, |args| args.text(5).map(Value::text_owned)).unwrap();
    say("host.fn.arg.out-of-range", evaluate("HOST_OOB(\"x\")", None).unwrap_err().code);
    let mut nested = Value::int(1);
    for _ in 0..300 { nested = Value::list(vec![nested]); }
    let root = Value::none();
    root.set("V", nested, Pos::default()).unwrap();
    let outcomes: Vec<_> = ["RECORD(TRUE, V)", "RECORD(\"k\", V)"].into_iter().map(|source| {
        let error = compile(source).unwrap().run(Some(root.clone())).unwrap_err();
        format!("{} {}:{}", error.code, error.pos.line, error.pos.col)
    }).collect();
    say("program.run.over-deep-host-value.key-error-first", &outcomes.join("|"));

    for line in out {
        println!("{}", line);
    }
}
