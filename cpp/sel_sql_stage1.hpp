// Stage 1: does SEL itself accept this expression, and can the program be
// reduced to ONE expression. Internal to the SQL layer.
//
// Two files' worth of the Python host -- constants.py and normalise.py -- kept
// together here because neither is large and each calls the other.
//
// **constants** answers a question the translator was not asking. Its job is
// "can this rule be pushed into that database", and it used to answer that
// without ever asking whether the rule is VALID. `LEFT("abc", -1)` translates
// cleanly into every dialect, and SEL raises E_RANGE for it while MariaDB
// answers '', PostgreSQL answers 'ab' and SQLite answers 'abc'. Three
// databases, three answers, none of them SEL's -- from a translation that
// reported success. So where an expression's arguments are all literals, the
// values SEL would reject are known here, and this asks SEL.
//
// It is validation, not constant folding. The value is computed and thrown
// away; the translation that follows is byte-identical to the one that would
// have been emitted without the check. Folding would have been the tempting
// second step and would have blinded the oracle: an expression replaced by its
// answer no longer exercises the database's version of the operation, which is
// the only thing sql/oracle/ exists to compare.
//
// **normalise** is the restrictive step. SEL is an expression language, but it
// has assignment and `;`, and SQL has neither. A helper variable is inlined as
// the expression it held, and anything that cannot be is refused with a
// position.

#ifndef SEL_SQL_STAGE1_HPP
#define SEL_SQL_STAGE1_HPP

#include <set>
#include <string>

#include "sel_sql.hpp"
#include "sel_sql_node.hpp"

namespace sel::sql {

// The scalar `value` bindings, as a name set and an evaluation root.
//
// A value binding is a constant the translator HAS -- docs/SQL-TRANSLATION.md
// §5.4 calls it "a constant supplied at translation time, inlined as a literal"
// -- so `LEFT("abc", X)` with X bound to "-1" is exactly as knowable as
// `LEFT("abc", -1)`, and before this it was exactly as wrong.
//
// Only scalars are lifted. A list-valued binding is what an aggregate iterates
// and its shape is the translator's business, not the evaluator's.
struct ConstScope {
  std::set<std::string> names;
  // The evaluation root. Program::run(root) is the whole of what the Python
  // host reaches Context and eval_node for -- Program::run IS
  // `Context ctx(root); eval_node(ast, ctx)`, so the public API is the entry
  // point and the SQL layer needs nothing internal to the evaluator.
  sel::Value root = sel::Value::none();
};

class Bindings;
ConstScope const_scope(const Bindings* bindings);

// A binder ARGUMENT, not a read of one: `ALL(V, C, C > 0)` names C in argument
// 1. Grouping is what tells them apart -- `ALL(V, (C), C > 0)` passes the VALUE
// of C, which SEL refuses with E_EXPECT_SYMBOL, and which used to translate to
// working SQL for a rule that can never run. A translation accepted where the
// language refuses is the one direction this layer must never fail in.
bool is_binder_name(const Node& n);
bool is_binder_name(const SNode& n);

// Whether every leaf under `n` is a literal. A binder an aggregate introduces
// inside `n` counts as bound, so `ALL((1, 2), _ > 0)` is constant and
// `ALL(ITEMS, _ > 0)` is not -- the same rule the evaluator applies, which is
// what lets the whole node be handed to it.
bool is_constant(const SNode& n, const std::set<std::string>& bound);

// Evaluate `n` the way SEL would, and refuse the translation if SEL refuses.
//
// Called AFTER the node has been translated, not before, so that every refusal
// the translator already had keeps its own message. `TRUE + 1` is an expression
// SEL rejects and also a BOOL where a number is required; the second is the
// more useful sentence and is the one an author can act on. This check adds
// refusals where translation used to SUCCEED -- the whole of the defect it
// exists for -- and changes none that already existed.
//
// The position reported is SEL's own: the innermost node that failed, not the
// outermost one this was called with, because that is the character the author
// has to change.
void validate(const SNode& n, sel::Value& root);

// Turn a program into one expression, or refuse it.
//
// Can return a bare clist -- `R[1] = 1; R` -- which the translator then refuses
// as a shape. The result is not always an expression node.
SNodePtr normalise(const NodePtr& ast, const std::set<std::string>& const_names,
                   sel::Value& root);

}  // namespace sel::sql

#endif  // SEL_SQL_STAGE1_HPP
