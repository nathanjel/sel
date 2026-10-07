use std::collections::HashMap;
use std::sync::{Arc, OnceLock, RwLock};

use crate::args::Args;
use crate::manifest::lookup_builtin;
use crate::utf8::SelError;
use crate::value::Value;

pub mod binary;
pub mod core;
pub mod math;
pub mod regex_ops;
pub mod structure;
pub mod text;

pub type BuiltinFn = fn(&mut Args) -> Result<Value, SelError>;

#[derive(Clone)]
pub enum SpecFn {
    Native(BuiltinFn),
    Host(Arc<dyn Fn(&mut Args) -> Result<Value, SelError> + Send + Sync>),
}

#[derive(Clone)]
pub struct Spec {
    pub name: String,
    pub min: usize,
    pub max: Option<usize>,
    pub lazy: bool,
    pub binds: bool,
    pub func: SpecFn,
}

impl std::fmt::Debug for Spec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Spec")
            .field("name", &self.name)
            .field("min", &self.min)
            .field("max", &self.max)
            .field("lazy", &self.lazy)
            .field("binds", &self.binds)
            .finish()
    }
}

impl Spec {
    pub fn call(&self, args: &mut Args) -> Result<Value, SelError> {
        match self.func {
            SpecFn::Native(f) => f(args),
            SpecFn::Host(ref f) => f(args),
        }
    }
}

fn register_native(m: &mut HashMap<String, Arc<Spec>>, name: &'static str, f: BuiltinFn) {
    let entry = lookup_builtin(name).unwrap_or_else(|| panic!("builtin {} not in manifest", name));
    m.insert(
        name.to_string(),
        Arc::new(Spec {
            name: name.to_string(),
            min: entry.min,
            max: entry.max,
            lazy: entry.lazy,
            binds: entry.binds,
            func: SpecFn::Native(f),
        }),
    );
}

/// A builtin that brings its own shape, as `define` does in the other hosts: a
/// name spec/builtins.json lists must agree with it (the manifest owns core's
/// shape; register_native() just reads it), and any other name -- a builtin of
/// the application's own -- passes through as declared here.
// Core registers through register_native(); define() is for a builtin beyond
// the manifest, added in registry() below. The stock build has none outside the
// unit tests -- the reference fragments in examples/fn-*/rust.rs are the uses,
// and tools/check-rust-fragments.sh compiles them in place -- hence the allow.
#[allow(dead_code)]
fn define(
    m: &mut HashMap<String, Arc<Spec>>,
    name: &'static str,
    min: usize,
    max: Option<usize>,
    lazy: bool,
    binds: bool,
    f: BuiltinFn,
) {
    if let Some(e) = lookup_builtin(name) {
        let mut wrong = Vec::new();
        if min != e.min {
            wrong.push(format!("min {} vs {}", min, e.min));
        }
        if max != e.max {
            wrong.push(format!("max {:?} vs {:?}", max, e.max));
        }
        if lazy != e.lazy {
            wrong.push(format!("lazy {} vs {}", lazy, e.lazy));
        }
        if binds != e.binds {
            wrong.push(format!("binds {} vs {}", binds, e.binds));
        }
        if !wrong.is_empty() {
            panic!("SEL function {} disagrees with spec/builtins.json: {}", name, wrong.join("; "));
        }
    }
    if m.contains_key(name) {
        panic!("SEL function {} defined twice", name);
    }
    m.insert(
        name.to_string(),
        Arc::new(Spec { name: name.to_string(), min, max, lazy, binds, func: SpecFn::Native(f) }),
    );
}

/// A builtin is whatever registry() registers natively -- core, and any builtin
/// define()d beside it -- not only what the manifest lists. This is the
/// registration question (register_function's replacement rule, host_arity),
/// never the effects one: see may_have_effects.
fn is_builtin(key: &str) -> bool {
    lookup_builtin(key).is_some()
        || registry().read().unwrap().get(key).is_some_and(|s| matches!(s.func, SpecFn::Native(_)))
}

/// The effects classification every analysis asks (spec/SPEC.md §8.1): whether
/// a call to `name` may keep, read or change values beyond its result, so that
/// no copy may be left out around it and nothing may be evaluated out of its
/// written order across it. Only a shipped builtin -- a name the manifest
/// lists, which registry() defines at startup and nothing replaces
/// (register_function refuses a builtin's name, define() a second definition)
/// -- is assumed not to. Every other function is the application's, however it
/// was installed: register_function, or a define() beside the manifest's,
/// strict, lazy or binding. Defining a function below the public API is not a
/// declaration that it is pure, and SpecFn::Native says only how it is called.
pub fn may_have_effects(name: &str) -> bool {
    // Names reach here canonical (upper case) from the parser: no allocation
    // unless one does not.
    lookup_builtin(name).is_none()
        && (!name.bytes().any(|b| b.is_ascii_lowercase()) || lookup_builtin(&name.to_ascii_uppercase()).is_none())
}

/// may_have_effects of what a call node runs: the definition it carries
/// (Node::spec, which the evaluator calls), else the registry's for its name.
/// A carried definition is shipped only when it is the very one registry()
/// made for a manifest name at startup; a Spec built beside it -- host or
/// native, under any name -- is the application's.
pub fn call_may_have_effects(node: &crate::ast::Node) -> bool {
    match &node.spec {
        Some(spec) => !shipped_specs().get(spec.name.as_str()).is_some_and(|s| Arc::ptr_eq(s, spec)),
        None => may_have_effects(&node.s),
    }
}

/// The definitions registry() made for the manifest's names, which nothing
/// replaces afterwards: read once, without the registry's lock.
fn shipped_specs() -> &'static HashMap<&'static str, Arc<Spec>> {
    static SHIPPED: OnceLock<HashMap<&'static str, Arc<Spec>>> = OnceLock::new();
    SHIPPED.get_or_init(|| {
        let reg = registry().read().unwrap();
        crate::manifest::builtins::BUILTINS.iter().map(|(name, _)| (*name, reg[*name].clone())).collect()
    })
}

fn registry() -> &'static RwLock<HashMap<String, Arc<Spec>>> {
    static REGISTRY: OnceLock<RwLock<HashMap<String, Arc<Spec>>>> = OnceLock::new();
    REGISTRY.get_or_init(|| {
        let mut m = HashMap::with_capacity(64);

        // Core
        register_native(&mut m, "IF", core::fn_if);
        register_native(&mut m, "COND", core::fn_cond);
        register_native(&mut m, "ABORT", core::fn_abort);
        register_native(&mut m, "IS_NULL", core::fn_is_null);
        register_native(&mut m, "IS_NOT_NULL", core::fn_is_not_null);
        register_native(&mut m, "COALESCE", core::fn_coalesce);
        register_native(&mut m, "GET", core::fn_get);
        register_native(&mut m, "PATH", core::fn_path);
        register_native(&mut m, "IS_BLANK", core::fn_is_blank);
        register_native(&mut m, "IS_PRESENT", core::fn_is_present);

        // Math
        register_native(&mut m, "ABS", math::fn_abs);
        register_native(&mut m, "SIGN", math::fn_sign);
        register_native(&mut m, "CEIL", math::fn_ceil);
        register_native(&mut m, "FLOOR", math::fn_floor);
        register_native(&mut m, "TRUNC", math::fn_trunc);
        register_native(&mut m, "CANON", math::fn_canon);
        register_native(&mut m, "ROUND", math::fn_round);
        register_native(&mut m, "POWER", math::fn_power);
        register_native(&mut m, "MIN", math::fn_min);
        register_native(&mut m, "MAX", math::fn_max);
        register_native(&mut m, "ISNUM", math::fn_isnum);

        // Binary
        register_native(&mut m, "BLEN", binary::fn_blen);
        register_native(&mut m, "TO_UTF8", binary::fn_to_utf8);
        register_native(&mut m, "FROM_UTF8", binary::fn_from_utf8);
        register_native(&mut m, "TO_HEX", binary::fn_to_hex);
        register_native(&mut m, "FROM_HEX", binary::fn_from_hex);
        register_native(&mut m, "ENCODE_BASE64", binary::fn_encode_base64);
        register_native(&mut m, "DECODE_BASE64", binary::fn_decode_base64);
        register_native(&mut m, "CRC32", binary::fn_crc32);
        register_native(&mut m, "BTL", binary::fn_btl);
        register_native(&mut m, "LTB", binary::fn_ltb);

        // Text
        register_native(&mut m, "LEN", text::fn_len);
        register_native(&mut m, "LEFT", text::fn_left);
        register_native(&mut m, "RIGHT", text::fn_right);
        register_native(&mut m, "SUBSTR", text::fn_substr);
        register_native(&mut m, "FIND", text::fn_find);
        register_native(&mut m, "REPLACE", text::fn_replace);
        register_native(&mut m, "SPLIT", text::fn_split);
        register_native(&mut m, "TRIM", text::fn_trim);
        register_native(&mut m, "LTRIM", text::fn_ltrim);
        register_native(&mut m, "RTRIM", text::fn_rtrim);
        register_native(&mut m, "UPPER", text::fn_upper);
        register_native(&mut m, "LOWER", text::fn_lower);
        register_native(&mut m, "BACKWARDS", text::fn_backwards);
        register_native(&mut m, "REPEAT", text::fn_repeat);
        register_native(&mut m, "PADL", text::fn_padl);
        register_native(&mut m, "PADR", text::fn_padr);
        register_native(&mut m, "CHAR", text::fn_char);
        register_native(&mut m, "CODE", text::fn_code);

        // Regex
        register_native(&mut m, "RMATCH", regex_ops::fn_rmatch);
        register_native(&mut m, "RFIND", regex_ops::fn_rfind);
        register_native(&mut m, "RGROUPS", regex_ops::fn_rgroups);
        register_native(&mut m, "RREPLACE", regex_ops::fn_rreplace);

        // Structure & Relational
        register_native(&mut m, "COUNT", structure::fn_count);
        register_native(&mut m, "INDEXES", structure::fn_indexes);
        register_native(&mut m, "HAS", structure::fn_has);
        register_native(&mut m, "LIST", structure::fn_list);
        register_native(&mut m, "RECORD", structure::fn_record);
        register_native(&mut m, "TAKE", structure::fn_take);
        register_native(&mut m, "DROP", structure::fn_drop);
        register_native(&mut m, "SELECT_COLS", structure::fn_select_cols);
        register_native(&mut m, "DEDUPE", structure::fn_dedupe);
        register_native(&mut m, "DISTINCT", structure::fn_distinct);
        register_native(&mut m, "MAP", structure::fn_map);
        register_native(&mut m, "FILTER", structure::fn_filter);
        register_native(&mut m, "ALL", structure::fn_all);
        register_native(&mut m, "ANY", structure::fn_any);
        register_native(&mut m, "SUM", structure::fn_sum);
        register_native(&mut m, "JOIN", structure::fn_join);
        register_native(&mut m, "SORT", structure::fn_sort);
        register_native(&mut m, "SORT_DESC", structure::fn_sort_desc);
        register_native(&mut m, "SORT_BY", structure::fn_sort_by);
        register_native(&mut m, "TOP", structure::fn_top);
        register_native(&mut m, "TOP_DESC", structure::fn_top_desc);
        register_native(&mut m, "TOP_BY", structure::fn_top_by);
        register_native(&mut m, "BUCKET", structure::fn_bucket);
        register_native(&mut m, "LINK", structure::fn_link);
        register_native(&mut m, "LINK_LEFT", structure::fn_link_left);

        // A builtin the manifest does not list: the unit tests below prove one
        // is a builtin like any other.
        #[cfg(test)]
        define(&mut m, "UNLISTED_ANY", 2, Some(3), true, true, structure::fn_any);
        // And three that write into the context, strict, lazy and binding: a
        // define()d function is no less the application's (may_have_effects).
        #[cfg(test)]
        {
            define(&mut m, "T_POKE", 0, Some(0), false, false, tests::poke);
            define(&mut m, "T_POKE_LAZY", 0, Some(0), true, false, tests::poke);
            define(&mut m, "T_POKE_EACH", 2, Some(3), true, true, tests::poke);
        }

        // spec/builtins.md: once registration is complete a host refuses to
        // start with a manifest name it never defined (register_native() above
        // already refuses one the manifest lacks).
        let missing: Vec<&str> =
            crate::manifest::builtins::BUILTINS.iter().map(|(name, _)| *name).filter(|name| !m.contains_key(*name)).collect();
        assert!(missing.is_empty(), "spec/builtins.json lists functions this host never defined: {}", missing.join(", "));

        RwLock::new(m)
    })
}

pub fn lookup_spec(name: &str) -> Option<Arc<Spec>> {
    let key = name.to_ascii_uppercase();
    registry().read().unwrap().get(&key).cloned()
}

pub fn is_reserved(name: &str) -> bool {
    crate::ops::is_reserved(name)
}

pub fn function_names() -> Vec<String> {
    let reg = registry().read().unwrap();
    let mut names: Vec<String> = reg.keys().cloned().collect();
    names.sort();
    names
}

pub fn register_function<F>(name: &str, min: i64, max: i64, f: F) -> Result<(), SelError>
where
    F: Fn(&mut Args) -> Result<Value, SelError> + Send + Sync + 'static,
{
    let bytes = name.as_bytes();
    let is_ascii_letter = |b: u8| b.is_ascii_lowercase() || b.is_ascii_uppercase();
    let is_ident = |b: u8| is_ascii_letter(b) || b.is_ascii_digit() || b == b'_';
    if bytes.is_empty() || !is_ascii_letter(bytes[0]) || !bytes.iter().all(|&b| is_ident(b)) {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("SEL function name must be ASCII letters, digits and _, starting with a letter: {:?}", name),
            crate::utf8::Pos::default(),
        ));
    }
    let key = name.to_ascii_uppercase();
    if is_reserved(&key) {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("{} is a reserved word", key),
            crate::utf8::Pos::default(),
        ));
    }
    if is_builtin(&key) {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("{} is a builtin; a host function cannot replace it", key),
            crate::utf8::Pos::default(),
        ));
    }
    if min < 0 || max < min {
        return Err(SelError::new(
            "E_BAD_ARG",
            format!("SEL function {}: arity must be whole numbers with 0 <= min <= max", key),
            crate::utf8::Pos::default(),
        ));
    }
    let spec = Spec {
        name: key.clone(),
        min: min as usize,
        max: Some(max as usize),
        lazy: false,
        binds: false,
        func: SpecFn::Host(Arc::new(f)),
    };
    registry().write().unwrap().insert(key, Arc::new(spec));
    Ok(())
}

pub fn host_arity(name: &str) -> Option<(usize, usize)> {
    let key = name.to_ascii_uppercase();
    if is_builtin(&key) {
        return None;
    }
    let spec = lookup_spec(&key)?;
    Some((spec.min, spec.max.unwrap_or(usize::MAX)))
}

pub fn reset_host_functions() {
    let mut reg = registry().write().unwrap();
    reg.retain(|_, s| matches!(s.func, SpecFn::Native(_)));
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::utf8::Pos;

    fn truth(src: &str) -> bool {
        crate::evaluate(src, None).unwrap().as_bool(Pos::default()).unwrap()
    }

    // UNLISTED_ANY is registry()'s test-only define() of ANY's body under a name
    // spec/builtins.json does not have -- as examples/fn-complex's FIRST would be.
    #[test]
    fn a_builtin_the_manifest_does_not_list_is_a_builtin() {
        assert!(lookup_builtin("UNLISTED_ANY").is_none());
        assert!(truth("UNLISTED_ANY(LIST(1, 2), X, X > 1)"));
        assert!(!truth("UNLISTED_ANY(LIST(1, 2), _ > 5)"));
        // Its binder is bound, not a dependency: the classic binding shapes apply.
        let deps = crate::compile("UNLISTED_ANY(ITEMS, X, X > LIMIT)").unwrap().dependencies().unwrap();
        assert_eq!(deps, vec!["ITEMS".to_string(), "LIMIT".to_string()]);
        // Its declared arity is checked at compile time.
        assert_eq!(crate::compile("UNLISTED_ANY(1)").unwrap_err().code, "E_ARITY");
        // A host function cannot replace it, it is not one, and a reset keeps it.
        assert_eq!(register_function("unlisted_any", 1, 1, |_| Ok(Value::bool(true))).unwrap_err().code, "E_BAD_ARG");
        assert!(host_arity("UNLISTED_ANY").is_none());
        reset_host_functions();
        assert!(lookup_spec("UNLISTED_ANY").is_some());
    }

    /// T_POKE, T_POKE_LAZY and T_POKE_EACH, registry()'s test-only define()s: a
    /// write an application's function may make (spec §3.4) -- the context's
    /// X[1]["k"] becomes 9 -- and the answer "1".
    pub(super) fn poke(args: &mut Args) -> Result<Value, SelError> {
        if let Some(first) = args.ctx.root.get("X").and_then(|x| x.get("1")) {
            first.set("k", Value::int(9), args.pos())?;
        }
        Ok(Value::text_owned("1".into()))
    }

    fn poke_context() -> Value {
        crate::evaluate(r#"RECORD("X", LIST(RECORD("k", 1), RECORD("k", 2)), "Y", LIST(RECORD("j", 1, "b", "1")))"#, None)
            .unwrap()
    }

    /// Programs around `call`, a call that pokes, with what each answers when
    /// no copy was left out around the call and nothing ran out of its
    /// written order: FILTER's rows are copies (the brief's probe; FILTER
    /// copies outside a borrowing pipeline whatever it calls), TOP_BY collects
    /// X[1] before the second key writes it (structure's may_write), and the
    /// outer LINK reads its left source before the right one that pokes
    /// (join_pure_source).
    fn poke_probes(call: &str) -> [(String, &'static str); 3] {
        [
            (format!(r#"FILTER(X, TRUE)[{call}]["k"]"#), r#"t"1""#),
            (format!(r#"TOP_BY(X, IF(_["k"] == 2, {call}, "0"), 2)[1]["k"]"#), r#"t"1""#),
            (
                format!(r#"X .> LINK(Y, _1["k"] == _2["j"]) .> LINK(LIST(RECORD("b", {call})), _["b"] == _2["b"]) .> FILTER(_["k"] == 1) .> MAP(1)"#),
                r#"-{"1"=t"1"}"#,
            ),
        ]
    }

    #[test]
    fn a_function_defined_beside_the_manifest_brings_the_copies_back() {
        // Spec §8.1: defining a function below the public API is not a
        // declaration that it is pure -- strict, lazy or binding (the
        // `F(LIST(1), _)` form of examples/fn-complex).
        let mut wrong = Vec::new();
        for call in ["T_POKE()", "T_POKE_LAZY()", "T_POKE_EACH(LIST(1), _)"] {
            for (source, expected) in poke_probes(call) {
                let out = crate::compile(&source).unwrap().run(Some(poke_context())).unwrap().dump().unwrap();
                if out != expected {
                    wrong.push(format!("{source} => {out}"));
                }
            }
        }
        assert!(wrong.is_empty(), "{wrong:#?}");
    }

    #[cfg(feature = "sql")]
    #[test]
    fn a_function_defined_beside_the_manifest_cannot_write_the_hybrid_callers_context() {
        use crate::sql::{execute_hybrid, plan_hybrid, Options};
        for call in ["T_POKE()", "T_POKE_LAZY()", "T_POKE_EACH(LIST(1), _)"] {
            let plan = plan_hybrid(&crate::compile(call).unwrap(), "sqlite", None, Options::default());
            assert!(plan.pure_memory, "{call}");
            let caller = poke_context();
            let out = execute_hybrid(&plan, |_, _| panic!("pure memory must not query SQL"), Some(&caller)).unwrap();
            assert_eq!(out.scalar(), "1", "{call}");
            assert_eq!(caller.get("X").unwrap().get("1").unwrap().get("k").unwrap().scalar(), "1", "{call}");
        }
    }

    #[test]
    fn only_a_shipped_builtin_is_assumed_to_have_no_effects() {
        assert!(!may_have_effects("FILTER"));
        assert!(!may_have_effects("is_null"));
        assert!(may_have_effects("T_NOT_DEFINED_ANYWHERE"));
        // A define() beside the manifest is a builtin for registration (no SQL
        // arity, not replaceable), but no less able to write.
        for name in ["T_POKE", "t_poke_lazy", "T_POKE_EACH", "UNLISTED_ANY"] {
            assert!(may_have_effects(name), "{name}");
            assert!(host_arity(name).is_none(), "{name}");
        }
        // What a node runs is the definition it carries: registry()'s own for
        // a shipped name, or one built beside it under the same name.
        let program = crate::compile("IS_NULL(1)").unwrap();
        assert!(!call_may_have_effects(program.ast()));
        let mut bare = program.ast().clone();
        bare.spec = None;
        assert!(!call_may_have_effects(&bare));
        let mut carried = program.ast().clone();
        carried.spec = Some(Arc::new((**carried.spec.as_ref().unwrap()).clone()));
        assert!(call_may_have_effects(&carried));
    }

    #[test]
    fn a_shipped_builtin_still_leaves_the_copies_out() {
        // A FILTER lends its rows to the MAP after it when both bodies call
        // only shipped builtins (the optimiser's read_only_expression), and a
        // join reads a shipped-only right source first.
        let mut program = crate::compile(r#"X .> FILTER(_["k"] > 0) .> MAP(RECORD("v", _["k"]))"#).unwrap();
        assert!(program.physical_ast().items[0].borrowed_filter);
        assert!(crate::join_prefilter::join_pure_source(crate::compile(r#"LIST(RECORD("b", ABS(1)))"#).unwrap().ast()));
        assert!(!crate::join_prefilter::join_pure_source(crate::compile(r#"LIST(RECORD("b", T_POKE()))"#).unwrap().ast()));
    }

    #[test]
    #[should_panic(expected = "disagrees with spec/builtins.json: lazy false vs true")]
    fn define_refuses_a_listed_name_with_another_shape() {
        define(&mut HashMap::new(), "ANY", 2, Some(3), false, true, structure::fn_any);
    }

    #[test]
    #[should_panic(expected = "defined twice")]
    fn define_refuses_a_name_twice() {
        let mut m = HashMap::new();
        define(&mut m, "UNLISTED_TWICE", 1, Some(1), false, false, text::fn_len);
        define(&mut m, "UNLISTED_TWICE", 1, Some(1), false, false, text::fn_len);
    }
}
