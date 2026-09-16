// Optimizer module boundary.
//
// The evaluator remains a supported drop-in single translation unit: the
// optimizer's rewrite machinery lives in sel.cpp beside the private decimal
// primitives it uses for exact constant folding. This file owns the public
// entry points and is included at that boundary, so both the normal library
// build and tools that include sel.cpp get one identical optimizer without
// duplicating the evaluator or exposing the decimal core.

NodePtr optimize_ast_logical(const NodePtr& ast) { return opt_root(ast, false); }
NodePtr optimize_ast_in_memory(const NodePtr& ast) { return opt_root(ast, true); }
NodePtr optimize_ast(const NodePtr& ast) { return optimize_ast_in_memory(ast); }

// The pipeline vocabulary the SQL planner shares with the optimizer. See
// sel_ast.hpp.
bool is_pipeline_op(std::string_view name) { return opt_pipeline_op(name); }
std::pair<NodePtr, std::vector<NodePtr>> unwind_pipeline(const NodePtr& root) {
  return opt_unwind(root);
}
NodePtr build_pipeline(NodePtr source, const std::vector<NodePtr>& steps) {
  return opt_build_pipeline(std::move(source), steps);
}
