use sel_lang::compile;

#[test]
fn dependencies_follow_definite_assignment_and_lazy_control_flow() {
    for (source, expected) in [
        ("A + 1; A = 2", "A"),
        ("X += 1", "X"),
        ("A[1] += 1", "A"),
        ("A[1] = 2", ""),
        ("A = A + 1", "A"),
        ("A = 1; A + B", "B"),
        ("IF(X, A = 1, 0); A", "A X"),
        ("IF(X, A = 1, A = 2); A", "X"),
        ("X AND (A = 1); A", "A X"),
        ("X OR (A = 1); A", "A X"),
        ("X ?? (A = 1); A", "A X"),
        ("X ??? (A = 1); A", "A X"),
        ("MAP(L, A = _); A", "A L"),
        ("COND(X, A = 1, Y, A = 2, A = 3); A", "X Y"),
        ("LEFT(\"abc\", (N = 2)); N", ""),
        ("COALESCE(X, A = 1, A); A", "A X"),
        ("COALESCE(A = 1, A); A", ""),
        ("GET(X, K, A = 1); A", "A K X"),
        ("PATH(X, K, A = 1); A", "A K X"),
        ("MAP(L, R, R[\"x\"] + A)", "A L"),
        ("MAP(L, R, (A = 1; A)); A", "A L"),
        ("A[(K = 1)][K] = B; K", "B"),
        ("A = (B = 1); B", ""),
        ("TOP(L, N, (N = 2)); N", "L N"),
        ("SORT_BY(L, (D), (D = \"ASC\")); D", "D L"),
        ("BUCKET(L, A = _, A); A", "A L"),
        ("COND(X, A, (B = Y), B, A = 2); B", "A B X Y"),
    ] {
        let program = compile(source).unwrap();
        let actual = program.dependencies().unwrap().join(" ");
        assert_eq!(actual, expected, "{source}");
        assert_eq!(
            program.dependencies().unwrap().join(" "),
            expected,
            "repeat: {source}"
        );
    }
}

#[test]
fn dependency_depth_failure_matches_logical_evaluation() {
    use sel_lang::eval::eval_node;
    use sel_lang::{Context, Pos, Value};
    let source = format!("{}A", "A + ".repeat(220));
    let program = compile(&source).unwrap();
    let root = Value::none();
    root.set("A", Value::int(1), Pos::default()).unwrap();
    let execution_error = eval_node(program.ast(), &mut Context::new(root)).unwrap_err();
    let analysis_error = program.dependencies().unwrap_err();
    assert_eq!(analysis_error.code, "E_DEPTH");
    assert_eq!(analysis_error.pos, execution_error.pos);
}

#[test]
fn dependency_assignment_path_depth_matches_runtime() {
    use sel_lang::eval::eval_node;
    use sel_lang::{Context, Value};
    let program = compile(&format!("A{} = 2", "[1]".repeat(200))).unwrap();
    let execution_error = eval_node(program.ast(), &mut Context::new(Value::none())).unwrap_err();
    let analysis_error = program.dependencies().unwrap_err();
    assert_eq!(analysis_error.code, "E_DEPTH");
    assert_eq!(analysis_error.pos, execution_error.pos);
}
