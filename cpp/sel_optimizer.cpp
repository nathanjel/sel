// Optimizer module boundary.
//
// The evaluator remains a supported drop-in single translation unit: the
// optimizer's rewrite machinery lives in sel.cpp beside the private decimal
// primitives it uses for exact constant folding. This file owns the public
// entry points and is included at that boundary, so both the normal library
// build and tools that include sel.cpp get one identical optimizer without
// duplicating the evaluator or exposing the decimal core.

NodePtr optimize_ast_logical(const NodePtr& ast) { return opt_tree(ast, false, 1); }
NodePtr optimize_ast_in_memory(const NodePtr& ast) { return opt_tree(ast, true, 1); }
NodePtr optimize_ast(const NodePtr& ast) { return optimize_ast_in_memory(ast); }
