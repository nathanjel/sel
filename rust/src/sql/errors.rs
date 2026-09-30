use std::fmt;
use crate::utf8::Pos;

#[derive(Clone, Debug, PartialEq)]
pub struct SqlError {
    pub code: String,
    pub message: String,
    pub pos: Pos,
}

impl SqlError {
    pub fn new(code: impl Into<String>, message: impl Into<String>, pos: Pos) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            pos,
        }
    }
}

impl fmt::Display for SqlError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        if self.pos.line > 0 && self.pos.col > 0 {
            write!(f, "{} at {}:{}: {}", self.code, self.pos.line, self.pos.col, self.message)
        } else {
            write!(f, "{}: {}", self.code, self.message)
        }
    }
}

impl std::error::Error for SqlError {}

pub fn refuse<T>(code: impl Into<String>, message: impl Into<String>, pos: Pos) -> Result<T, SqlError> {
    Err(SqlError::new(code, message, pos))
}
