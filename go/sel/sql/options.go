package sql

// The typed configuration API: the same bindings and dialects as ColumnBinding,
// RawBinding, RelationBinding, RelationQueryBinding and DefineDialect, with
// every flag named rather than positional and no interface{} argument. The
// positional constructors remain (the documentation and the examples use them)
// and are what these call.

// ColumnOptions is what an application may say about a column (docs/sql.md,
// "bindings"): Exact, the column already compares bytes exactly; Sargable, a
// comparison on it may use an index; Guard, its values may not be numbers, so
// numeric use is guarded; Collation, "binary", "exact", "sargable" or
// "default", a spelling that implies the two flags; Prefilter, how a sargable prefilter is emitted; SplitSargable, emit
// that prefilter as a separate conjunct.
type ColumnOptions struct {
	Exact         bool
	Sargable      bool
	Guard         bool
	Collation     string
	Prefilter     string
	SplitSargable bool
}

// Column binds a name to column of table (table may be ""): ColumnBinding with
// its options named.
func Column(column, table string, typ SqlKind, opts ColumnOptions) *Binding {
	return ColumnBinding(column, table, typ, opts.Exact, opts.Sargable, opts.Guard, opts.Collation, opts.Prefilter, opts.SplitSargable)
}

// RawColumn binds a name to a SQL expression the application wrote: RawBinding
// with its options named.
func RawColumn(sql string, typ SqlKind, opts ColumnOptions) *Binding {
	return RawBinding(sql, typ, opts.Exact, opts.Sargable, opts.Guard, opts.Collation, opts.Prefilter, opts.SplitSargable)
}

// RelationOptions is what an application may say about a relation: Scalar and
// Correlate name the fields that make a row's scalar and correlate it with an
// outer query, Prefilter and SplitSargable are as for a column.
type RelationOptions struct {
	Scalar        string
	Correlate     string
	Prefilter     string
	SplitSargable bool
}

// Relation binds a name to a table whose rows have these fields, in order:
// RelationBinding with a typed field list and its options named.
func Relation(from, alias string, fields []FieldEntry, opts RelationOptions) *Binding {
	return RelationBinding(from, alias, fields, opts.Scalar, opts.Correlate, opts.Prefilter, opts.SplitSargable)
}

// RelationQuery binds a name to the rows of a query the application wrote:
// RelationQueryBinding with a typed field list and its options named.
func RelationQuery(query, alias string, fields []FieldEntry, opts RelationOptions) *Binding {
	return RelationQueryBinding(query, alias, fields, opts.Scalar, opts.Correlate, opts.Prefilter, opts.SplitSargable)
}

// DialectOptions declares a dialect for DefineDialectWith. Extends names the
// dialect it inherits from ("" for one with no parent, which must then give a
// Version); Version is dotted-numeric ("" inherits the parent's); Target false
// declares a base other dialects inherit from rather than a server (nil is
// true); Lexical overrides lexical keys of the parent's.
type DialectOptions struct {
	Extends string
	Version string
	Target  *bool
	Lexical map[string]interface{}
}

// DefineDialectWith is DefineDialect with a typed declaration.
func DefineDialectWith(name string, opts DialectOptions) {
	spec := map[string]interface{}{"extends": nil}
	if opts.Extends != "" {
		spec["extends"] = opts.Extends
	}
	if opts.Version != "" {
		spec["version"] = opts.Version
	}
	if opts.Target != nil {
		spec["target"] = *opts.Target
	}
	if opts.Lexical != nil {
		spec["lexical"] = opts.Lexical
	}
	DefineDialect(name, spec)
}
