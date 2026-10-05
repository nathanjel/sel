package manifest

// Hand-written beside the generated builtins.go: how a call's arguments are
// matched to the binding forms of spec/builtins.json, and which argument plays
// which part in the sort family. The evaluator, the optimiser, the dependency
// walk and the SQL statement compiler all read a call's form here.

// ArgShape tells MatchForm what argument i looks like in the tree: a bare
// (ungrouped) name, or a text literal. The two node types differ by package.
type ArgShape interface {
	IsName(i int) bool
	IsText(i int) bool
}

// MatchForm is the binding form of a call of name with count arguments: the
// first form of that count whose condition holds, in the manifest's order (a
// text literal in the direction slot is tested before a name in the binder
// slot, so SORT_BY(L, K, "DESC") is key and direction), or nil.
func MatchForm(name string, count int, args ArgShape) *Form {
	for i := range BindingForms[name] {
		f := &BindingForms[name][i]
		if f.Count != count {
			continue
		}
		switch f.WhenKind {
		case WhenName:
			if !args.IsName(f.WhenArg) {
				continue
			}
		case WhenText:
			if !args.IsText(f.WhenArg) {
				continue
			}
		}
		return f
	}
	return nil
}

// SortRoles are the argument positions of a SORT, SORT_DESC, SORT_BY, TOP,
// TOP_DESC or TOP_BY call: -1 for a part the form does not have. Binder is the
// name slot, Key the key the rows are ordered by, Dir the direction (SORT_BY
// and TOP_BY only) and Limit the count (the TOPs: always the last argument).
type SortRoles struct {
	Binder, Key, Dir, Limit int
}

// Sort decodes the parts of a sort-family call from its form, or reports false
// for a name outside the family or a count no form has.
func Sort(name string, count int, args ArgShape) (SortRoles, bool) {
	r := SortRoles{Binder: -1, Key: -1, Dir: -1, Limit: -1}
	top := false
	switch name {
	case "SORT", "SORT_DESC", "SORT_BY":
	case "TOP", "TOP_DESC", "TOP_BY":
		top = true
	default:
		return r, false
	}
	f := MatchForm(name, count, args)
	if f == nil {
		return r, false
	}
	last := count
	if top {
		r.Limit = count - 1
		last = count - 1
	}
	for i := 1; i < last; i++ {
		switch f.Scopes[i] {
		case ScopeBinder:
			r.Binder = i
		case ScopeInner:
			r.Key = i
		case ScopeOuter:
			if r.Key >= 0 && r.Dir < 0 {
				r.Dir = i
			}
		}
	}
	return r, true
}
