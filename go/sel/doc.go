// Package sel is the Go implementation of SEL, a small expression language for
// business rules — validation, pricing, eligibility, routing, reporting.
//
// A rule is text. Seven independent implementations run it (Python, JavaScript,
// PHP, C++, Common Lisp, Rust and this one), held to one specification and one
// conformance suite, and they agree to the byte: on the value, and on the error
// code and position when a rule fails. Arithmetic is exact decimal, text is
// strict UTF-8, and there is no truthiness. The SQL layer, package
// github.com/nathanjel/sel/go/sel/sql, compiles the same rules to SQL.
//
// # Compile once, run many times
//
// [Compile] parses a rule and catches syntax errors, unknown functions and wrong
// argument counts before any data is involved; the [Program] it returns is
// immutable and safe to share between goroutines. [Program.Run] evaluates it
// against a context, a [Value] whose children are the rule's variables. A
// context is built child by child with [NewNone] and [Value.Set]; money and every
// other number is passed as text ([NewText]), never as a float64. A run assigns
// into the context it is given, so a rule can hand values back.
//
// # Errors
//
// [Compile], [Program.Run] and [Eval] return an error, which is always a
// *[SelError]: a stable Code (E_NOT_NUM, E_ABORT, …) and the position of the node
// that failed. Match on the code, never on the message.
//
// Everything else reports a failure by panicking with a *[SelError], the way an
// index out of range does: the accessors of a value ([Value.AsText],
// [Value.AsBool], [Value.Decimal], …) when it is not of that kind, the
// constructors and [Value.Set] when given what SEL refuses (invalid UTF-8, keys
// and values that do not pair up), and [Program.Dependencies] for a program
// nested too deeply to walk (E_DEPTH). Code that reads results it does not control —
// a rule that might answer TRUE where text was expected — recovers that panic.
// Inside a host function ([RegisterFunction]) panicking is the way to fail: call
// [Fail], or let an [Args] reader do it, and Run returns the error.
// RegisterFunction itself panics with a plain string for a name or an arity that
// is not allowed, a programming error found at start-up.
//
// # Numbers
//
// A number is text: pass it with NewText("19.99") and read it with AsText. In the
// decimal form other hosts take ({neg, digits, scale}) it is a [Decimal], built
// with [NewDecimal] and read with [Value.Decimal]. NewNum and AsDecimal, which
// took and returned the module's internal decimal type, are deprecated.
//
// # The syntax tree
//
// [Node], [NodeType] and its constants, [NewNode], [Node.Copy], [NewProgram], [Program.AST],
// [Program.PhysicalAST], [UnwindPipeline], [BuildPipeline], [OptimizeAstLogical],
// [OptimizeAstInMemory], [BindingForm], [HostArity], [MayHaveEffects], [ValidatePattern] and
// [ValidatePatternFlags] are exported because the SQL layer
// (github.com/nathanjel/sel/go/sel/sql) and the module's own tools read the
// syntax tree, and Go has no narrower visibility between packages. They are not
// part of the API this module keeps stable: an application compiles rules with
// [Compile] and does not build or walk trees.
//
// # More
//
// The documentation, with every example in all seven languages:
// https://github.com/nathanjel/sel/blob/main/docs/usage/README.md. The command-line
// REPL: go install github.com/nathanjel/sel/go/bin/sel@latest.
package sel
