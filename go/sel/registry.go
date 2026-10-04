// The function table. Fixed at startup.

package sel

import (
	"fmt"
	"regexp"
	"sort"
	"sync"

	"github.com/nathanjel/sel/go/internal/manifest"
	"github.com/nathanjel/sel/go/internal/utf8"
)

var hostNameRegex = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9_]*$`)

var (
	registryMu sync.RWMutex
	funcTable  = make(map[string]*Spec)
	hostFuncs  = make(map[string]struct{})
)

func Define(spec *Spec) {
	key := utf8.AsciiUpper(spec.Name)
	registryMu.Lock()
	defer registryMu.Unlock()

	if _, exists := funcTable[key]; exists {
		panic(fmt.Sprintf("SEL function %s defined twice", key))
	}

	m, ok := manifest.Builtins[key]
	if ok {
		var wrong []string
		if spec.Min != m.Min {
			wrong = append(wrong, fmt.Sprintf("min %d vs %d", spec.Min, m.Min))
		}
		if spec.Max != m.Max {
			wrong = append(wrong, fmt.Sprintf("max %d vs %d", spec.Max, m.Max))
		}
		if spec.Lazy != m.Lazy {
			wrong = append(wrong, fmt.Sprintf("lazy %v vs %v", spec.Lazy, m.Lazy))
		}
		if spec.Binds != m.Binds {
			wrong = append(wrong, fmt.Sprintf("binds %v vs %v", spec.Binds, m.Binds))
		}
		if spec.ArityError != nil {
			wrong = append(wrong, "an arity rule of its own, which the manifest owns")
		}
		if len(wrong) > 0 {
			panic(fmt.Sprintf("SEL function %s disagrees with spec/builtins.json: %s", key, stringsJoin(wrong, "; ")))
		}
		spec.ArityError = m.ArityError
	}

	funcTable[key] = spec
}

func stringsJoin(elems []string, sep string) string {
	if len(elems) == 0 {
		return ""
	}
	s := elems[0]
	for _, e := range elems[1:] {
		s += sep + e
	}
	return s
}

// The manifest (spec/builtins.json) names every built-in; each is defined by some
// module's init. Definition-by-definition mismatches are refused in Define, but a
// name no module defined would only surface as an unknown function at parse time.
// So the first lookup, by which every init has run, checks coverage once and keeps
// refusing if it failed (GO-C41; JS does the same in assertManifestCovered).
var (
	manifestOnce    sync.Once
	manifestMissing string
)

func assertManifestCovered() {
	manifestOnce.Do(func() {
		registryMu.RLock()
		defer registryMu.RUnlock()
		var missing []string
		for name := range manifest.Builtins {
			if _, ok := funcTable[name]; !ok {
				missing = append(missing, name)
			}
		}
		if len(missing) > 0 {
			sort.Strings(missing)
			manifestMissing = fmt.Sprintf("spec/builtins.json names %s but no module defines it", stringsJoin(missing, ", "))
		}
	})
	if manifestMissing != "" {
		panic(manifestMissing)
	}
}

func Lookup(name string) *Spec {
	assertManifestCovered()
	key := utf8.AsciiUpper(name)
	registryMu.RLock()
	defer registryMu.RUnlock()
	return funcTable[key]
}

type BindingFormResult struct {
	Scopes []manifest.Scope
	Binds  []string
}

func BindingForm(name string, args []*Node, spec *Spec) *BindingFormResult {
	key := utf8.AsciiUpper(name)
	forms, ok := manifest.BindingForms[key]
	if !ok {
		if spec == nil {
			spec = Lookup(key)
		}
		if spec == nil || !spec.Binds {
			return nil
		}
		// Generic forms for host-defined binding functions
		if len(args) == 2 {
			return &BindingFormResult{
				Scopes: []manifest.Scope{manifest.ScopeOuter, manifest.ScopeInner},
				Binds:  []string{"_", "_K"},
			}
		}
		if len(args) == 3 && args[1].T == NodeVar && !args[1].Grouped {
			return &BindingFormResult{
				Scopes: []manifest.Scope{manifest.ScopeOuter, manifest.ScopeBinder, manifest.ScopeInner},
				Binds:  []string{"_K", args[1].S},
			}
		}
		return nil
	}

	for _, f := range forms {
		if f.Count != len(args) {
			continue
		}
		if f.WhenArg >= 0 {
			a := args[f.WhenArg]
			var matches bool
			if f.WhenKind == manifest.WhenName {
				matches = a.T == NodeVar && !a.Grouped
			} else if f.WhenKind == manifest.WhenText {
				matches = a.T == NodeText
			}
			if !matches {
				continue
			}
		}
		bound := make([]string, len(f.Binds))
		copy(bound, f.Binds)
		for i, sc := range f.Scopes {
			if sc == manifest.ScopeBinder && args[i].T == NodeVar {
				bound = append(bound, args[i].S)
			}
		}
		return &BindingFormResult{
			Scopes: f.Scopes,
			Binds:  bound,
		}
	}
	return nil
}

func RegisterFunction(name string, min, max int, fn func(args *Args) *Value) {
	if !hostNameRegex.MatchString(name) {
		panic(fmt.Sprintf("SEL function name must be ASCII letters, digits and _, starting with a letter: %q", name))
	}
	key := utf8.AsciiUpper(name)
	if _, isRes := reserved[key]; isRes {
		panic(fmt.Sprintf("%s is a reserved word", key))
	}
	// SPEC 8: a non-callable function is refused when it is registered (the host's
	// startup-error class), never left to panic later inside Run.
	if fn == nil {
		panic(fmt.Sprintf("SEL function %s: the function must be callable, got nil", key))
	}
	registryMu.Lock()
	defer registryMu.Unlock()
	if _, exists := funcTable[key]; exists {
		if _, isHost := hostFuncs[key]; !isHost {
			panic(fmt.Sprintf("%s is a builtin; a host function cannot replace it", key))
		}
	}
	if min < 0 || max < min {
		panic(fmt.Sprintf("SEL function %s: arity must be whole numbers with 0 <= min <= max", key))
	}

	call := func(args *Args, ctx *Context) *Value {
		return fn(args)
	}

	funcTable[key] = &Spec{
		Name: key,
		Min:  min,
		Max:  max,
		Fn:   call,
	}
	hostFuncs[key] = struct{}{}
}

func HostArity(name string) (int, int, bool) {
	key := utf8.AsciiUpper(name)
	registryMu.RLock()
	defer registryMu.RUnlock()
	if _, ok := hostFuncs[key]; !ok {
		return 0, 0, false
	}
	spec := funcTable[key]
	return spec.Min, spec.Max, true
}

// isHostFunction reports whether name is an application-registered function
// (which may have effects) rather than a builtin.
func isHostFunction(name string) bool {
	key := utf8.AsciiUpper(name)
	_, inManifest := manifest.Builtins[key]
	return !inManifest
}
