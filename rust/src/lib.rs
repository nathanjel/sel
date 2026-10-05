#![doc = include_str!("../README.md")]

// The API this crate promises is what the documentation shows
// (docs/usage/*.md, README.md, the examples):
//
//   - the crate root's re-exports below: compile/evaluate/Program, Value and
//     its Entry/Kind, Args, Pos/SelError, registering host functions, the
//     decimal type Dec with its arithmetic, and Context (a REPL's session
//     state, for Program::run_with_context);
//   - `sql` (the default `sql` feature), `limits` and `text::SelStr`.
//
// Every other module is `#[doc(hidden)]`: the evaluator's internals, public
// only so this repository's own tests (rust/tests), harness (rust/dev) and
// benchmarks can reach them. They carry no compatibility promise and may
// change in any release; an application that needs something from one should
// ask for it to be made part of the API above.
//
// Sharing: Value is a single-threaded handle (Rc<RefCell<_>>, neither Send nor
// Sync), and so is anything holding one. A compiled Program is Send but not
// Sync: move it to a thread, or compile (or clone) one per thread. The probes
// below hold the crate to that; README.md says the same to its users.

// Clippy runs clean (`cargo clippy -p sel-lang`). These lints are allowed on
// purpose, crate-wide:
// - unnecessary_unwrap, needless_range_loop, needless_late_init,
//   collapsible_match: the shapes they flag are transcriptions of the other
//   hosts' code (the "same shape in every host" rule) or index loops over
//   parallel arrays, kept readable side by side rather than idiomatic;
// - type_complexity: the planner's tuple types are spelt where they are used;
// - result_large_err: the regex counter's error is cold and built once;
// - enum_variant_names, len_without_is_empty, new_without_default: internal
//   types whose names and API mirror the other hosts';
// - boxed_local: the box keeps a recursive frame small (the evaluator must
//   reach E_DEPTH on a small stack).
#![allow(
    clippy::unnecessary_unwrap,
    clippy::needless_range_loop,
    clippy::needless_late_init,
    clippy::collapsible_match,
    clippy::type_complexity,
    clippy::result_large_err,
    clippy::enum_variant_names,
    clippy::len_without_is_empty,
    clippy::new_without_default,
    clippy::boxed_local
)]

#[doc(hidden)]
pub mod args;
#[doc(hidden)]
pub mod ast;
#[doc(hidden)]
pub mod builtins;
#[doc(hidden)]
pub mod context;
pub mod dec;
#[doc(hidden)]
pub mod eval;
#[doc(hidden)]
pub mod join_plan;
#[doc(hidden)]
pub mod join_prefilter;
mod large_dec;
#[doc(hidden)]
pub mod lexicon;
pub mod limits;
#[doc(hidden)]
pub mod manifest;
#[doc(hidden)]
pub mod math_ops;
#[doc(hidden)]
pub mod math_plan;
mod ops;
#[doc(hidden)]
pub mod optimizer;
#[doc(hidden)]
pub mod parser;
#[doc(hidden)]
pub mod program;
#[doc(hidden)]
pub mod regex;
mod regex_ambiguity;
mod regex_counter;
#[doc(hidden)]
pub mod shape;
#[cfg(feature = "sql")]
pub mod sql;
pub mod text;
#[doc(hidden)]
pub mod utf8;
#[doc(hidden)]
pub mod value;

pub use args::Args;
pub use context::Context;
pub use builtins::{
    function_names, host_arity, is_reserved, lookup_spec, register_function, reset_host_functions,
    Spec,
};
pub use dec::{
    dec_abs, dec_add, dec_ceil, dec_cmp, dec_div, dec_floor, dec_format, dec_guard, dec_mod,
    dec_mul, dec_negate, dec_parse, dec_power, dec_round, dec_sign, dec_sub, dec_trim_scale,
    dec_trunc, Dec,
};
pub use program::{compile, evaluate, Program};
pub use utf8::{decode_utf8_source, Pos, SelError};
pub use value::{Entry, Kind, Value};

// Compile-time sharing probes (see the comment at the top). A Program may move
// to another thread; nothing that holds a Value may, and nothing here is Sync.
const _: () = {
    const fn assert_send<T: Send>() {}
    assert_send::<Program>();
    assert_send::<Dec>();
    assert_send::<SelError>();
    assert_send::<Pos>();
};
#[cfg(test)]
mod sharing {
    // Negative probes: were the type Send (Sync), the call would have two
    // candidate impls and fail to compile as ambiguous.
    trait NotSend<A> {
        fn probe() {}
    }
    impl<T: ?Sized> NotSend<()> for T {}
    struct IsSend;
    impl<T: ?Sized + Send> NotSend<IsSend> for T {}

    trait NotSync<A> {
        fn probe() {}
    }
    impl<T: ?Sized> NotSync<()> for T {}
    struct IsSync;
    impl<T: ?Sized + Sync> NotSync<IsSync> for T {}

    #[test]
    fn value_is_neither_send_nor_sync_and_program_is_not_sync() {
        <crate::Value as NotSend<_>>::probe();
        <crate::Value as NotSync<_>>::probe();
        <crate::Program as NotSync<_>>::probe();
    }
}
