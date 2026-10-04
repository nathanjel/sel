//! Scalar text storage for values.
//!
//! A value's text never changes after the value is built (the one later write
//! is the lazy formatting of a computed number, which fills an empty text), so
//! text can be immutable and shared: a copy of a value may point at the same
//! bytes, and no program can tell. `SelStr` keeps up to 22 bytes inline -- no
//! allocation, which covers most field values -- and shares longer text through
//! an `Rc<str>`, so cloning never copies it. It is 24 bytes, the size of a
//! `String`.
//!
//! Only whole strings are ever stored: text is never cut at the inline limit,
//! so an inline string is always valid UTF-8.
//!
//! # Migration from enum representation
//!
//! `SelStr` previously exposed `Inline` and `Shared` enum variants. To prevent
//! construction of invalid UTF-8 and protect representation invariants, the
//! internal representation is now private.
//! - To construct a `SelStr`, use [`SelStr::new`], [`SelStr::from`], or [`SelStr::EMPTY`].
//! - To inspect whether storage is inline, use [`SelStr::is_inline`].
//! - To read the string slice, use [`SelStr::as_str`] or the [`Deref`] / [`AsRef`] implementations.

use std::borrow::Borrow;
use std::cmp::Ordering;
use std::fmt;
use std::hash::{Hash, Hasher};
use std::ops::Deref;
use std::rc::Rc;

const INLINE: usize = 22;

/// Scalar text storage for values.
///
/// Representation is private to guarantee valid UTF-8 and invariant enforcement.
/// External code cannot construct invalid internal storage:
///
/// ```compile_fail
/// use sel_lang::text::SelStr;
///
/// let _ = SelStr::Inline {
///     len: 1,
///     buf: [0xff; 22],
/// };
/// ```
#[derive(Clone)]
pub struct SelStr(Repr);

#[derive(Clone)]
enum Repr {
    Inline { len: u8, buf: [u8; INLINE] },
    Shared(Rc<str>),
}

impl SelStr {
    pub const EMPTY: Self = Self(Repr::Inline { len: 0, buf: [0; INLINE] });

    pub fn new(s: &str) -> Self {
        if s.len() <= INLINE {
            let mut buf = [0u8; INLINE];
            buf[..s.len()].copy_from_slice(s.as_bytes());
            Self(Repr::Inline { len: s.len() as u8, buf })
        } else {
            Self(Repr::Shared(Rc::from(s)))
        }
    }

    #[inline]
    pub fn as_str(&self) -> &str {
        match &self.0 {
            // SAFETY: All representations originate from complete, valid strings (see `new`).
            Repr::Inline { len, buf } => unsafe {
                std::str::from_utf8_unchecked(&buf[..*len as usize])
            },
            Repr::Shared(s) => s,
        }
    }

    /// Whether the text is held inline (no allocation behind it).
    pub fn is_inline(&self) -> bool {
        matches!(&self.0, Repr::Inline { .. })
    }
}

impl Default for SelStr {
    fn default() -> Self {
        SelStr::EMPTY
    }
}

impl Deref for SelStr {
    type Target = str;
    #[inline]
    fn deref(&self) -> &str {
        self.as_str()
    }
}

impl AsRef<str> for SelStr {
    fn as_ref(&self) -> &str {
        self.as_str()
    }
}

impl Borrow<str> for SelStr {
    fn borrow(&self) -> &str {
        self.as_str()
    }
}

impl From<&str> for SelStr {
    fn from(s: &str) -> Self {
        SelStr::new(s)
    }
}

impl From<String> for SelStr {
    fn from(s: String) -> Self {
        SelStr::new(&s)
    }
}

impl From<&String> for SelStr {
    fn from(s: &String) -> Self {
        SelStr::new(s)
    }
}

impl PartialEq for SelStr {
    fn eq(&self, other: &Self) -> bool {
        self.as_str() == other.as_str()
    }
}

impl Eq for SelStr {}

impl PartialEq<str> for SelStr {
    fn eq(&self, other: &str) -> bool {
        self.as_str() == other
    }
}

impl PartialEq<&str> for SelStr {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == *other
    }
}

impl PartialOrd for SelStr {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for SelStr {
    fn cmp(&self, other: &Self) -> Ordering {
        self.as_str().cmp(other.as_str())
    }
}

// Hashes exactly as the `str` it holds, so it can key maps looked up by `&str`.
impl Hash for SelStr {
    fn hash<H: Hasher>(&self, state: &mut H) {
        self.as_str().hash(state)
    }
}

impl fmt::Debug for SelStr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Debug::fmt(self.as_str(), f)
    }
}

impl fmt::Display for SelStr {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        fmt::Display::fmt(self.as_str(), f)
    }
}
