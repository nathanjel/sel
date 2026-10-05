//! What each operator IS -- the one place this host asks.
//!
//! Every answer comes from the lexicon rendering (`crate::lexicon`, generated
//! from spec/lexicon.json by tools/gen-lexicon.mjs): the evaluator, the
//! constant folder, the optimiser, the join pre-filter, the dependency walker
//! and the SQL layer classify an operator through these functions instead of
//! keeping string lists of their own. The lookups are the generated `match`es,
//! so a question costs what the hand-written `matches!` it replaced did.

use crate::lexicon::{self, Family, Op, Relation};

/// The record of a binary operator (a `Bin` node's `s`): an infix operator of
/// the lexicon that builds a binary node -- not an assignment, `,` or `;`.
#[inline]
pub(crate) fn binary(op: &str) -> Option<&'static Op> {
    lexicon::infix_symbol(op)
        .or_else(|| lexicon::infix_word(op))
        .filter(|o| o.node == "bin")
}

#[inline]
fn family(op: &str) -> Option<Family> {
    binary(op).map(|o| o.family)
}

/// `==` `!=` `<` `<=` `>` `>=` and their `$` twins (§4.5, §5.3).
#[inline]
pub(crate) fn is_comparison(op: &str) -> bool {
    matches!(family(op), Some(Family::Compare | Family::TextCompare))
}

/// `==` … `>=`: compare numerically (§4.5).
#[inline]
pub(crate) fn is_numeric_comparison(op: &str) -> bool {
    family(op) == Some(Family::Compare)
}

/// `$==` … `$>=`: compare bytewise (§5.3).
#[inline]
pub(crate) fn is_text_comparison(op: &str) -> bool {
    family(op) == Some(Family::TextCompare)
}

/// `EQL` `IN` (§5.4).
#[inline]
pub(crate) fn is_deep_comparison(op: &str) -> bool {
    family(op) == Some(Family::DeepCompare)
}

/// `+` `-` `*` `/` `%` as binary operators (§5.1).
#[inline]
pub(crate) fn is_arithmetic(op: &str) -> bool {
    family(op) == Some(Family::Arith)
}

/// `AND` `OR` `XOR` (§5.6).
#[inline]
pub(crate) fn is_logic(op: &str) -> bool {
    family(op) == Some(Family::Logic)
}

/// `&` (§5.2).
#[inline]
pub(crate) fn is_concat(op: &str) -> bool {
    family(op) == Some(Family::Concat)
}

/// `AND`, `OR`, `??`, `???`: the right operand may never run.
#[inline]
pub(crate) fn is_short_circuit(op: &str) -> bool {
    binary(op).is_some_and(|o| o.short_circuit)
}

/// A comparison's relation (`eq` … `ge`), for both comparison families.
#[inline]
pub(crate) fn relation(op: &str) -> Option<Relation> {
    binary(op).and_then(|o| o.relation)
}

/// Whether an ordering satisfies a relation.
#[inline]
pub(crate) fn holds(rel: Relation, c: std::cmp::Ordering) -> bool {
    match rel {
        Relation::Eq => c.is_eq(),
        Relation::Ne => c.is_ne(),
        Relation::Lt => c.is_lt(),
        Relation::Le => c.is_le(),
        Relation::Gt => c.is_gt(),
        Relation::Ge => c.is_ge(),
    }
}

/// The binary operator a compound assignment applies (`+=` applies `+`); None
/// for `=` and for anything that is not an assignment operator.
#[inline]
pub(crate) fn compound(op: &str) -> Option<&'static str> {
    lexicon::infix_symbol(op)
        .filter(|o| o.family == Family::Assign)
        .and_then(|o| o.compound)
}

/// A reserved word (§2.3).
#[inline]
pub(crate) fn is_reserved(word: &str) -> bool {
    lexicon::RESERVED.contains(&word)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::lexicon::{Fixity, OPS};

    // The families as the hand-written lists had them, so a lexicon edit that
    // moves an operator between families is seen here, not in a wrong answer.
    #[test]
    fn families_match_the_spec_tables() {
        let names = |p: fn(&str) -> bool| -> Vec<&str> {
            OPS.iter().filter(|o| o.fixity == Fixity::Infix).map(|o| o.token).filter(|t| p(t)).collect()
        };
        assert_eq!(names(is_numeric_comparison), ["==", "!=", "<", "<=", ">", ">="]);
        assert_eq!(names(is_text_comparison), ["$==", "$!=", "$<", "$<=", "$>", "$>="]);
        assert_eq!(names(is_deep_comparison), ["EQL", "IN"]);
        assert_eq!(names(is_arithmetic), ["*", "/", "%", "+", "-"]);
        assert_eq!(names(is_logic), ["AND", "XOR", "OR"]);
        assert_eq!(names(is_short_circuit), ["??", "???", "AND", "OR"]);
        assert_eq!(compound("+="), Some("+"));
        assert_eq!(compound("="), None);
        assert!(binary("=").is_none() && binary(",").is_none() && binary(";").is_none());
        assert!(binary("NEG").is_none() && binary("NOT").is_none());
    }

    // Every binary operator of the lexicon has a branch in the evaluator: a
    // missing one would answer "unknown operator" (E_SYNTAX) at run time.
    #[test]
    fn evaluator_handles_every_binary_operator() {
        for o in OPS.iter().filter(|o| o.fixity == Fixity::Infix && o.node == "bin") {
            for (l, r) in [("1", "1"), ("TRUE", "FALSE"), ("TO_UTF8(\"a\")", "TO_UTF8(\"b\")"), ("\"a\"", "\"b\"")] {
                let src = format!("{l} {} {r}", o.token);
                if let Err(e) = crate::program::evaluate(&src, None) {
                    assert!(!e.message.contains("unknown operator"), "{}: {}", o.token, e.message);
                }
            }
        }
    }
}
