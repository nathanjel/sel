// The SEL->SQL conformance suite for the Go host.
//
// Run from the repository root:
//
//     go run ./bin/sqlt                 every case
//     go run ./bin/sqlt bind. agg.      only cases whose name contains one of these
//     go run ./bin/sqlt --names         what this host loaded, and stop
//
// The cases live in sql/cases/*.sqlt and reach here through
// tools/gen-sql-cases.mjs, which is the only thing that reads them. Nothing in
// this file parses anything: a case's bindings and registrations arrive as
// typed constructor calls the compiler has already checked.

package main

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/nathanjel/sel/go/sel"
	"github.com/nathanjel/sel/go/sel/sql"
)

var mirrors = map[string]string{
	"mariadb": "mysql",
}

func throwsIsKnown(name string) bool {
	return name == "LogicException"
}

type suiteError struct {
	msg string
}

func (e suiteError) Error() string {
	return e.msg
}

type expected struct {
	code   string
	hasPos bool
	line   int
	col    int
}

func parseExpected(s, at string) (expected, error) {
	sp := strings.IndexByte(s, ' ')
	if sp < 0 {
		return expected{code: s}, nil
	}
	code := s[:sp]
	pos := strings.TrimSpace(s[sp+1:])
	colon := strings.IndexByte(pos, ':')
	if colon < 0 {
		return expected{}, suiteError{msg: at + ": malformed error expectation"}
	}
	line, err := strconv.Atoi(pos[:colon])
	if err != nil {
		return expected{}, suiteError{msg: at + ": malformed error expectation"}
	}
	col, err := strconv.Atoi(pos[colon+1:])
	if err != nil {
		return expected{}, suiteError{msg: at + ": malformed error expectation"}
	}
	return expected{code: code, hasPos: true, line: line, col: col}, nil
}

func countSlots(s string) int {
	n := 0
	for i := 0; i+2 < len(s); i++ {
		if s[i] != '~' {
			continue
		}
		j := i + 1
		for j < len(s) && s[j] >= '0' && s[j] <= '9' {
			j++
		}
		if j > i+1 && j < len(s) && s[j] == '~' {
			n++
			i = j
		}
	}
	return n
}

func joinDumps(vs []*sel.Value) string {
	var out []string
	for _, v := range vs {
		out = append(out, v.Dump())
	}
	return strings.Join(out, ", ")
}

func classify(plan *sql.HybridPlan) string {
	if plan.PureSql {
		return "pure_sql"
	}
	if plan.PureMemory {
		return "pure_memory"
	}
	return "hybrid"
}

func joinTables(tables []string) string {
	return "[" + strings.Join(tables, ", ") + "]"
}

func equalTables(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func parseMode(modeName, at string) (sql.Mode, error) {
	switch strings.ToLower(strings.TrimSpace(modeName)) {
	case "inline":
		return sql.ModeInline, nil
	case "params":
		return sql.ModeParams, nil
	case "debug":
		return sql.ModeDebug, nil
	default:
		return 0, suiteError{msg: at + ": unknown mode " + modeName}
	}
}

func runPlanCase(c SqlCase, dialect string) (problem string, sErr error) {
	modeName := "inline"
	if c.Mode != nil {
		modeName = *c.Mode
	}
	mode, err := parseMode(modeName, c.At)
	if err != nil {
		return "", err
	}

	var (
		haveError bool
		sqlErr    *sql.SqlError
		plan      *sql.HybridPlan
		prog      *sel.Program
	)

	execErr := func() (resErr error) {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sql.SqlError); ok {
					sqlErr = se
					haveError = true
				} else if se, ok := r.(sql.SqlError); ok {
					sqlErr = &se
					haveError = true
				} else {
					resErr = suiteError{msg: fmt.Sprintf("%s: unexpected throw: %v", c.At, r)}
				}
			}
		}()
		if c.RegisterFn != nil {
			c.RegisterFn()
		}
		var binds *sql.Bindings
		if c.BindingsFn != nil {
			binds = sql.NewBindings(c.BindingsFn())
		} else {
			binds = sql.NewBindings(nil)
		}
		p, compileErr := sel.Compile(c.Source)
		if compileErr != nil {
			return fmt.Errorf("the source did not compile: %v", compileErr)
		}
		prog = p
		opts := sql.Options{Strict: c.Strict}
		plan = sql.PlanHybrid(prog, dialect, binds, opts)
		return nil
	}()

	if execErr != nil {
		if se, ok := execErr.(suiteError); ok {
			return "", se
		}
		return execErr.Error(), nil
	}

	wantPlan := *c.Plan
	if wantPlan == "refused" {
		if !haveError {
			return fmt.Sprintf("expected %s, got a %s plan", *c.Error, classify(plan)), nil
		}
		want, err := parseExpected(*c.Error, c.At)
		if err != nil {
			return "", err
		}
		if sqlErr.Code != want.code {
			return fmt.Sprintf("expected %s, got %s (%s)", want.code, sqlErr.Code, sqlErr.Message), nil
		}
		return "", nil
	}

	if haveError {
		return fmt.Sprintf("expected a %s plan, got %s (%s)", wantPlan, sqlErr.Code, sqlErr.Message), nil
	}

	got := classify(plan)
	if got != wantPlan {
		return fmt.Sprintf("expected a %s plan, got %s", wantPlan, got), nil
	}
	if c.HasTables && !equalTables(plan.SourceTables, c.Tables) {
		return fmt.Sprintf("source tables got:  %s\n     want: %s", joinTables(plan.SourceTables), joinTables(c.Tables)), nil
	}
	if plan.Dialect != dialect {
		return fmt.Sprintf("plan.dialect is %s, not %s", plan.Dialect, dialect), nil
	}

	if wantPlan == "pure_memory" {
		if plan.SqlStatement != nil {
			return "a pure-memory plan carries a SQL statement", nil
		}
		if plan.ContinuationProgram == nil || plan.ContinuationProgram.AST() != prog.AST() {
			return "a pure-memory plan must run the original program", nil
		}
		if plan.ContinuationAst != prog.AST() {
			return "a pure-memory plan must expose the original AST as its continuation", nil
		}
	} else {
		if plan.SqlStatement == nil {
			return fmt.Sprintf("a %s plan has no SQL statement", wantPlan), nil
		}
		if plan.SqlPrefixAst == nil {
			return fmt.Sprintf("a %s plan has no SQL prefix AST", wantPlan), nil
		}
		sqlStr := plan.SqlStatement.AsStatement(mode)
		wantExpect := ""
		if c.Expect != nil {
			wantExpect = *c.Expect
		}
		if sqlStr != wantExpect {
			return fmt.Sprintf("got:  %s\n     want: %s", sqlStr, wantExpect), nil
		}
		if wantPlan == "pure_sql" {
			if plan.ContinuationProgram != nil || plan.ContinuationAst != nil {
				return "a pure-SQL plan carries a continuation", nil
			}
			if plan.IsHybrid {
				return "a pure-SQL plan reports is_hybrid", nil
			}
		} else {
			if plan.ContinuationProgram == nil || plan.ContinuationAst == nil {
				return "a hybrid plan has no continuation", nil
			}
			if !plan.IsHybrid {
				return "a hybrid plan does not report is_hybrid", nil
			}
		}
	}

	sel.OptimizeAstInMemory(prog.AST())
	return "", nil
}

func runCase(c SqlCase, dialect string) (problem string, sErr error) {
	if dialect == "" {
		return "", suiteError{msg: fmt.Sprintf("%s: case %s has no --- dialect", c.At, c.Name)}
	}
	as := "value"
	if c.As != nil {
		as = *c.As
	}
	modeName := "inline"
	if c.Mode != nil {
		modeName = *c.Mode
	}
	mode, err := parseMode(modeName, c.At)
	if err != nil {
		return "", err
	}

	var (
		haveSql     bool
		haveError   bool
		haveThrown  bool
		sqlStr      string
		thrownWhat  string
		sqlErr      *sql.SqlError
		frag        *sql.Fragment
		prog        *sel.Program
		binds       *sql.Bindings
	)

	opts := sql.Options{Strict: c.Strict}

	func() {
		defer func() {
			if r := recover(); r != nil {
				if se, ok := r.(*sql.SqlError); ok {
					sqlErr = se
					haveError = true
				} else if se, ok := r.(sql.SqlError); ok {
					sqlErr = &se
					haveError = true
				} else {
					thrownWhat = fmt.Sprintf("%v", r)
					haveThrown = true
				}
			}
		}()

		if c.RegisterFn != nil {
			c.RegisterFn()
		}
		if c.BindingsFn != nil {
			binds = sql.NewBindings(c.BindingsFn())
		} else {
			binds = sql.NewBindings(nil)
		}
		p, compileErr := sel.Compile(c.Source)
		if compileErr != nil {
			thrownWhat = "the source did not compile: " + compileErr.Error()
			haveThrown = true
			return
		}
		prog = p
		f, trErr := sql.Translate(prog, dialect, binds, opts)
		if trErr != nil {
			if se, ok := trErr.(*sql.SqlError); ok {
				sqlErr = se
				haveError = true
				return
			}
			thrownWhat = trErr.Error()
			haveThrown = true
			return
		}
		frag = f
		switch as {
		case "condition":
			sqlStr = frag.AsCondition(mode)
		case "statement":
			sqlStr = frag.AsStatement(mode)
		default:
			sqlStr = frag.AsValue(mode)
		}
		haveSql = true
	}()

	if c.Throws != nil {
		if !throwsIsKnown(*c.Throws) {
			return "", suiteError{msg: fmt.Sprintf("%s: no Go equivalent is recorded for --- throws %s", c.At, *c.Throws)}
		}
		if !haveThrown {
			got := sqlStr
			if haveError {
				got = sqlErr.Error()
			}
			return fmt.Sprintf("expected %s, got %s", *c.Throws, got), nil
		}
		return "", nil
	}
	if haveThrown {
		if strings.HasPrefix(thrownWhat, "the source did not compile: ") {
			return thrownWhat, nil
		}
		return "", suiteError{msg: fmt.Sprintf("%s: unexpected throw: %s", c.At, thrownWhat)}
	}

	if as == "statement" && prog != nil {
		var (
			twinHasError bool
			twinSql      string
			twinErr      *sql.SqlError
		)
		twinThrew := func() (threw string) {
			defer func() {
				if r := recover(); r != nil {
					if se, ok := r.(*sql.SqlError); ok {
						twinErr = se
						twinHasError = true
					} else if se, ok := r.(sql.SqlError); ok {
						twinErr = &se
						twinHasError = true
					} else {
						threw = fmt.Sprintf("%v", r)
					}
				}
			}()
			f, tErr := sql.TranslateStatement(prog, dialect, binds, opts)
			if tErr != nil {
				if se, ok := tErr.(*sql.SqlError); ok {
					twinErr = se
					twinHasError = true
					return ""
				}
				threw = tErr.Error()
				return threw
			}
			twinSql = f.AsStatement(mode)
			return ""
		}()

		if twinThrew != "" {
			return "", suiteError{msg: fmt.Sprintf("%s: translate_statement threw: %s", c.At, twinThrew)}
		}

		gotDesc := func(has bool, e *sql.SqlError) string {
			if has {
				return fmt.Sprintf("%s at %d:%d", e.Code, e.Line(), e.Col())
			}
			return "SQL"
		}
		if haveError || twinHasError {
			if !haveError || !twinHasError || sqlErr.Code != twinErr.Code ||
				sqlErr.Line() != twinErr.Line() || sqlErr.Col() != twinErr.Col() {
				return fmt.Sprintf("translate() gave %s but translate_statement() gave %s",
					gotDesc(haveError, sqlErr), gotDesc(twinHasError, twinErr)), nil
			}
		} else if twinSql != sqlStr {
			return fmt.Sprintf("translate_statement() disagrees with translate():\n     %s\n     %s", twinSql, sqlStr), nil
		}
	}

	if c.Error != nil {
		if !haveError {
			return fmt.Sprintf("expected %s, got %s", *c.Error, sqlStr), nil
		}
		want, err := parseExpected(*c.Error, c.At)
		if err != nil {
			return "", err
		}
		if sqlErr.Code != want.code {
			return fmt.Sprintf("expected %s, got %s (%s)", want.code, sqlErr.Code, sqlErr.Message), nil
		}
		if want.hasPos {
			gotPos := fmt.Sprintf("%d:%d", sqlErr.Line(), sqlErr.Col())
			wantedPos := fmt.Sprintf("%d:%d", want.line, want.col)
			if gotPos != wantedPos {
				return fmt.Sprintf("expected %s at %s, got it at %s", want.code, wantedPos, gotPos), nil
			}
		}
		return "", nil
	}

	if haveError {
		return fmt.Sprintf("expected SQL, got %s (%s)", sqlErr.Code, sqlErr.Message), nil
	}
	wantExpect := ""
	if c.Expect != nil {
		wantExpect = *c.Expect
	}
	if !haveSql || sqlStr != wantExpect {
		return fmt.Sprintf("got:  %s\n     want: %s", sqlStr, wantExpect), nil
	}

	seen := make(map[int]bool)
	for _, p := range frag.Parts {
		if !p.IsSlot {
			continue
		}
		if p.Slot < 1 || p.Slot > len(frag.Params) {
			return fmt.Sprintf("parameter slot %d has no value in params", p.Slot), nil
		}
		seen[p.Slot] = true
	}
	var orphans []string
	for i := 1; i <= len(frag.Params); i++ {
		if !seen[i] {
			orphans = append(orphans, strconv.Itoa(i))
		}
	}
	if len(orphans) > 0 {
		return fmt.Sprintf("parameter slot(s) [%s] were bound but never emitted — a fragment was rendered and discarded", strings.Join(orphans, ", ")), nil
	}

	debugRender := ""
	switch as {
	case "statement":
		debugRender = frag.AsStatement(sql.ModeDebug)
	case "condition":
		debugRender = frag.AsCondition(sql.ModeDebug)
	default:
		debugRender = frag.AsValue(sql.ModeDebug)
	}
	if len(frag.Bindings()) != countSlots(debugRender) {
		return "bindings() and the emitted placeholders disagree in count", nil
	}

	if c.Params != nil {
		got := joinDumps(frag.Bindings())
		if got != *c.Params {
			return fmt.Sprintf("params got:  %s\n     want: %s", got, *c.Params), nil
		}
	}

	return "", nil
}

type failure struct {
	c       SqlCase
	problem string
}

func main() {
	filters := os.Args[1:]

	if len(filters) == 1 && filters[0] == "--names" {
		for _, c := range sqlCases {
			fmt.Printf("%s\t%s\n", c.At, c.Name)
		}
		return
	}

	passed := 0
	mirrored := 0
	compileRefused := 0
	suiteErrors := 0
	var failures []failure

	for _, c := range sqlCases {
		if len(filters) > 0 {
			wanted := false
			for _, f := range filters {
				if strings.Contains(c.Name, f) {
					wanted = true
					break
				}
			}
			if !wanted {
				continue
			}
		}

		if c.Unrepresentable != nil {
			passed++
			compileRefused++
			continue
		}

		sql.Reset()
		var problem string
		var sErr error
		if c.Plan != nil {
			problem, sErr = runPlanCase(c, c.Dialect)
		} else {
			problem, sErr = runCase(c, c.Dialect)
		}

		if sErr != nil {
			fmt.Printf("SUITE ERROR %s\n", sErr.Error())
			suiteErrors++
			continue
		}
		if problem != "" {
			failures = append(failures, failure{c: c, problem: problem})
			continue
		}
		passed++

		m, isMirrored := mirrors[c.Dialect]
		if !isMirrored || c.RegisterFn != nil {
			continue
		}
		sql.Reset()

		mariaCollation := " COLLATE utf8mb4_nopad_bin"
		mysqlCollation := " COLLATE utf8mb4_0900_bin"
		mirrorCase := c
		if c.Expect != nil {
			rep := strings.ReplaceAll(*c.Expect, mariaCollation, mysqlCollation)
			mirrorCase.Expect = &rep
		}

		if mirrorCase.Plan != nil {
			problem, sErr = runPlanCase(mirrorCase, m)
		} else {
			problem, sErr = runCase(mirrorCase, m)
		}

		if sErr != nil {
			fmt.Printf("SUITE ERROR (mirrored to %s) %s\n", m, sErr.Error())
			suiteErrors++
			continue
		}
		if problem == "" {
			mirrored++
		} else {
			failures = append(failures, failure{
				c:       c,
				problem: fmt.Sprintf("mirrored to %s, which must agree with %s: %s", m, c.Dialect, problem),
			})
		}
	}

	for _, f := range failures {
		fmt.Printf("FAIL %s  (%s)\n     %s\n", f.c.Name, f.c.At, f.problem)
	}

	fmt.Printf("\n%d passed (%d also checked against a mirrored dialect, %d refused by the type system), %d failed, %d suite errors\n",
		passed, mirrored, compileRefused, len(failures), suiteErrors)

	if len(failures) > 0 || suiteErrors > 0 {
		os.Exit(1)
	}
}
