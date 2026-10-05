use std::fmt;
use crate::utf8::Pos;
use crate::value::Value;
use crate::sql::errors::{refuse, SqlError};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Default)]
pub enum SqlKind {
    #[default]
    Unknown,
    Num,
    Text,
    Bool,
    Bin,
    List,
    Statement,
}

impl SqlKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            SqlKind::Num => "NUM",
            SqlKind::Text => "TEXT",
            SqlKind::Bool => "BOOL",
            SqlKind::Bin => "BIN",
            SqlKind::List => "LIST",
            SqlKind::Statement => "STATEMENT",
            SqlKind::Unknown => "UNKNOWN",
        }
    }

    pub fn from_name(name: &str) -> Self {
        match name.trim().to_ascii_uppercase().as_str() {
            "NUM" => SqlKind::Num,
            "TEXT" => SqlKind::Text,
            "BOOL" => SqlKind::Bool,
            "BIN" => SqlKind::Bin,
            "LIST" => SqlKind::List,
            "STATEMENT" => SqlKind::Statement,
            _ => SqlKind::Unknown,
        }
    }
}

impl fmt::Display for SqlKind {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Mode {
    #[default]
    Inline,
    Params,
    Debug,
}

impl Mode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Mode::Inline => "inline",
            Mode::Params => "params",
            Mode::Debug => "debug",
        }
    }

    /// Parse the public rendering mode names without silently choosing a mode.
    pub fn from_name(name: &str) -> Result<Self, ParseModeError> {
        name.parse()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ParseModeError {
    name: String,
}

impl fmt::Display for ParseModeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "unknown render mode {:?}; use inline, params or debug", self.name)
    }
}
impl std::error::Error for ParseModeError {}

impl std::str::FromStr for Mode {
    type Err = ParseModeError;

    fn from_str(name: &str) -> Result<Self, Self::Err> {
        match name {
            "inline" => Ok(Self::Inline),
            "params" => Ok(Self::Params),
            "debug" => Ok(Self::Debug),
            _ => Err(ParseModeError { name: name.to_owned() }),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Part {
    Sql(String),
    Slot(usize), // 1-based index into params
}

#[derive(Clone, Debug)]
pub struct Fragment {
    pub parts: Vec<Part>,
    pub kind: SqlKind,
    pub dialect: String,
    pub params: Vec<Value>,
    pub param_kinds: Vec<SqlKind>,
    pub caveats: Vec<String>,
    pub exact: bool,
    pub sargable: bool,
    pub guard: bool,
    pub prefilter: Option<Box<Fragment>>,
    pub separate_prefilter: bool,
    pub canonical: bool,
    pub whole_sum: bool,
}

impl Fragment {
    pub fn new(
        parts: Vec<Part>,
        kind: SqlKind,
        dialect: impl Into<String>,
        params: Vec<Value>,
        param_kinds: Vec<SqlKind>,
        caveats: Vec<String>,
    ) -> Self {
        Self {
            parts,
            kind,
            dialect: dialect.into(),
            params,
            param_kinds,
            caveats,
            exact: false,
            sargable: false,
            guard: false,
            prefilter: None,
            separate_prefilter: false,
            canonical: false,
            whole_sum: false,
        }
    }

    pub fn is_exact(&self) -> bool {
        self.caveats.is_empty()
    }

    pub fn is_inline(&self, slot: usize) -> bool {
        let kind = if slot > 0 && slot - 1 < self.param_kinds.len() {
            self.param_kinds[slot - 1]
        } else {
            SqlKind::Text
        };
        kind == SqlKind::Num || kind == SqlKind::Bool || kind == SqlKind::Bin
    }

    pub fn bindings(&self) -> Vec<Value> {
        let mut out = Vec::new();
        for p in &self.parts {
            if let Part::Slot(slot) = p {
                if !self.is_inline(*slot) && *slot > 0 && *slot - 1 < self.params.len() {
                    out.push(self.params[*slot - 1].clone());
                }
            }
        }
        out
    }

    pub fn as_value(&self, mode: Mode) -> Result<String, SqlError> {
        if self.kind == SqlKind::List {
            return refuse("E_SQL_SHAPE", "this expression yields a list, and a SQL expression is a scalar", Pos::default());
        }
        if self.kind == SqlKind::Statement {
            return refuse("E_SQL_SHAPE", "this expression yields a statement, and a SQL expression is a scalar; use as_statement()", Pos::default());
        }
        self.join_mode(mode)
    }

    pub fn as_statement(&self, mode: Mode) -> Result<String, SqlError> {
        if self.kind != SqlKind::Statement {
            return refuse("E_SQL_SHAPE", format!("expected STATEMENT fragment, got {}; use as_value() or as_condition()", self.kind), Pos::default());
        }
        self.join_mode(mode)
    }

    pub fn as_condition(&self, mode: Mode) -> Result<String, SqlError> {
        if self.kind == SqlKind::Bool {
            return self.join_mode(mode);
        }
        refuse("E_SQL_SHAPE", format!("a condition must be BOOL, and this expression is {}; SQL has no truthiness and neither does SEL", self.kind), Pos::default())
    }

    pub fn join_mode(&self, mode: Mode) -> Result<String, SqlError> {
        crate::sql::emit::join_fragment(self, mode)
    }
}

#[cfg(test)]
mod mode_tests {
    use super::*;

    #[test]
    fn rendering_modes_have_strict_fallible_names() {
        for mode in [Mode::Inline, Mode::Params, Mode::Debug] {
            assert_eq!(Mode::from_name(mode.as_str()), Ok(mode));
            assert_eq!(mode.as_str().parse::<Mode>(), Ok(mode));
        }
        for name in ["", "bogus", "INLINE", "Params", " inline", "debug ", "params\0"] {
            let error = Mode::from_name(name).unwrap_err();
            assert_eq!(error.name, name);
            assert!(error.to_string().contains("use inline, params or debug"));
        }
        // Defaulting is explicit in the type, never a fallback for bad input.
        assert_eq!(Mode::default(), Mode::Inline);
    }
}
