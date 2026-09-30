pub mod binder;
pub mod binding;
pub mod constants;
pub mod emit;
pub mod errors;
pub mod hybrid;
pub mod map;
pub mod map_data;
pub mod node;
pub mod normalise;
pub mod relational_plan;
pub mod row_model;
pub mod statement;
pub mod translator;
pub mod types;

pub use binding::{Binding, Bindings, ColumnSpec, FieldEntry, RelationSpec};
pub use emit::Emit;
pub use errors::{refuse, SqlError};
pub use hybrid::{
    execute_hybrid, plan_hybrid, try_translate_statement, HybridPlan, SelectedMember,
};
pub use map::*;
pub use relational_plan::{
    BucketState, RelationalFilter, RelationalGroup, RelationalJoin, RelationalOrder,
    RelationalPlan, RelationalProjection,
};
pub use translator::{Options, Translator};
pub use types::{Fragment, Mode, ParseModeError, Part, SqlKind};

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

pub fn must_translate(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> Fragment {
    translate(program, dialect, bindings, options).unwrap_or_else(|e| panic!("{}", e))
}

pub fn must_translate_statement(
    program: &Program,
    dialect: &str,
    bindings: Option<&Bindings>,
    options: Options,
) -> Fragment {
    translate_statement(program, dialect, bindings, options).unwrap_or_else(|e| panic!("{}", e))
}

pub fn dialects() -> Vec<String> {
    targets()
}
