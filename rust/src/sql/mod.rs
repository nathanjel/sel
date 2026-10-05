//! The SEL→SQL layer: a rule as a SQL condition, a pipeline as a statement, and
//! the hybrid planner that splits a pipeline between the database and memory.
//!
//! The default `sql` feature. A translation is emitted only when the SQL means
//! exactly what SEL means; anything else is refused with a [`SqlError`] whose
//! code says why (`E_SQL_UNSUPPORTED`, `E_SQL_BINDING`, …).
//!
//! ```
//! use sel_lang::compile;
//! use sel_lang::sql::{translate, Binding, Bindings, Mode, Options, SqlKind};
//! use std::collections::HashMap;
//!
//! # fn main() -> Result<(), Box<dyn std::error::Error>> {
//! // Describe where the rule's variables live: here, two columns of `orders`.
//! let column = |name: &str, kind| Binding::column_with(name, "orders", kind, Default::default());
//! let bindings = Bindings::new(Some(HashMap::from([
//!     ("QTY".to_string(), column("qty", SqlKind::Num)),
//!     ("STATUS".to_string(), column("status", SqlKind::Text)),
//! ])));
//!
//! let rule = compile(r#"QTY > 5 AND STATUS $== "open""#)?;
//! let fragment = translate(&rule, "postgresql", Some(&bindings), Options::default())?;
//! assert_eq!(
//!     fragment.as_condition(Mode::Params)?,
//!     r#"(("orders"."qty" > 5) AND (CAST("orders"."status" AS TEXT) COLLATE "C" = CAST(? AS TEXT) COLLATE "C"))"#
//! );
//! // In params mode every text literal is a placeholder; these are its values.
//! let values: Vec<String> = fragment.bindings().iter().map(|v| v.scalar()).collect();
//! assert_eq!(values, ["open"]);
//! # Ok(())
//! # }
//! ```
//!
//! Pipelines go through [`plan_hybrid`], which pushes the longest exact prefix
//! into SQL, and [`execute_hybrid`], which runs the plan with a runner the
//! application supplies (a closure from a statement and its parameters to rows).
//! The worked examples in the repository's `examples/sql-*` directories run
//! such plans against PostgreSQL, MariaDB and SQLite.

// The submodules are the translator's internals: public only for this
// repository's tests and harness, with no compatibility promise (see the
// crate root). The API is what this module re-exports and defines below.
#[doc(hidden)]
pub mod binder;
#[doc(hidden)]
pub mod binding;
#[doc(hidden)]
pub mod constants;
#[doc(hidden)]
pub mod emit;
#[doc(hidden)]
pub mod errors;
#[doc(hidden)]
pub mod hybrid;
#[doc(hidden)]
pub mod map;
#[doc(hidden)]
pub mod map_data;
#[doc(hidden)]
pub mod node;
#[doc(hidden)]
pub mod normalise;
#[doc(hidden)]
pub mod relational_plan;
#[doc(hidden)]
pub mod row_model;
#[doc(hidden)]
pub mod statement;
#[doc(hidden)]
pub mod translator;
#[doc(hidden)]
pub mod types;

/// The JSON crate the SQL layer is built on. `define`, `define_dialect` and
/// `define_builder` take a `serde_json::Value`; this re-export lets an
/// application build one (`sel_lang::sql::serde_json::json!`) without depending
/// on a `serde_json` of its own whose version might not match.
pub use serde_json;

pub use binding::{Binding, Bindings, ColumnOptions, ColumnSpec, FieldEntry, RelationSpec};
pub use emit::Emit;
pub use errors::SqlError;
pub use hybrid::{execute_hybrid, plan_hybrid, HybridPlan, SelectedMember};
pub use map::{
    chain, define, define_builder, define_dialect, exists, reset, targets, version, BuilderFn,
};
pub use translator::Options;
pub use types::{Fragment, Mode, ParseModeError, Part, SqlKind};

use translator::Translator;
use crate::program::Program;

pub fn translate(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> Result<Fragment, SqlError> {
    let default_bindings = Bindings::default();
    let catalog = bindings.unwrap_or(&default_bindings);
    let mut t = Translator::new(dialect, Some(catalog.clone()), options);
    t.translate(program.ast())
}

pub fn try_translate(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> Option<Fragment> {
    translate(program, dialect, bindings, options).ok()
}

pub fn translate_statement(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> Result<Fragment, SqlError> {
    let default_bindings = Bindings::default();
    let catalog = bindings.unwrap_or(&default_bindings);
    let mut t = Translator::new(dialect, Some(catalog.clone()), options);
    t.translate_statement(program.ast())
}

pub fn dialects() -> Vec<String> {
    targets()
}
