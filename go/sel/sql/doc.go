// Package sql is SEL's SQL layer: a rule as a SQL condition ([Translate]), a
// pipeline as a statement ([TranslateStatement]), and the hybrid planner
// ([PlanHybrid], [ExecuteHybrid]) that pushes the longest exact prefix of a
// pipeline into the database and finishes the rest in memory.
//
// A translation is emitted only when the SQL means exactly what SEL means — exact
// collations for text, guarded casts for numbers that may not be numbers,
// NULL-aware aggregates — and is refused otherwise with a *[SqlError] whose code
// says why. Dialects: MariaDB, MySQL, PostgreSQL and SQLite, and any an
// application derives from them ([DefineDialect], [Define], [DefineBuilder]).
//
// Where a rule's variables live is described by bindings: [ColumnBinding],
// [RawBinding], [ColumnsBinding], [RelationBinding], [RelationQueryBinding] and
// [ValueBinding], gathered with [NewBindings]. [Column], [RawColumn], [Relation],
// [RelationQuery] and [DefineDialectWith] are the same with their options named
// in a struct ([ColumnOptions], [RelationOptions], [DialectOptions]) instead of
// passed positionally.
//
// Translate, TryTranslate, TranslateStatement and ExecuteHybrid return their
// failure as an error. The configuration calls — the binding constructors,
// [PlanHybrid] with a dialect that does not exist, Define and DefineDialect —
// and the accessors of a [Fragment] (AsCondition, AsValue) panic instead, with a
// *[SqlError] (or, for a call naming a dialect that does not exist, a string).
//
// The design is described in docs/internals/sql-translation.md, the dialect map
// in sql/MAP.md and the error codes in sql/errors.md, in the repository
// https://github.com/nathanjel/sel.
package sql
