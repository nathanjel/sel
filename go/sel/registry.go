// The function table. Fixed at startup.

package sel

import (
	"fmt"
	"regexp"
	"sort"
	"strings"
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

// Define adds a builtin to the function table, as the shipped builtins are
// added (examples/fn-simple and examples/fn-complex show both kinds). An
// application's own functions are registered with RegisterFunction instead. A
// name outside spec/builtins.json defined here is no builtin to the analyses,
// though: it counts as able to change any value it reaches (MayHaveEffects).
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
			panic(fmt.Sprintf("SEL function %s disagrees with spec/builtins.json: %s", key, strings.Join(wrong, "; ")))
		}
		spec.ArityError = m.ArityError
	}

	funcTable[key] = spec
}

// The manifest (spec/builtins.json) names every built-in; each is defined by some
// module's init. Definition-by-definition mismatches are refused in Define, but a
// name no module defined would only surface as an unknown function at parse time.
// So the first lookup, by which every init has run, checks coverage once and keeps
// refusing if it failed (JS does the same in assertManifestCovered).
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
			manifestMissing = fmt.Sprintf("spec/builtins.json names %s but no module defines it", strings.Join(missing, ", "))
		}
	})
	if manifestMissing != "" {
		panic(manifestMissing)
	}
}

func lookup(name string) *Spec {
	assertManifestCovered()
	key := utf8.AsciiUpper(name)
	registryMu.RLock()
	defer registryMu.RUnlock()
	return funcTable[key]
}

// BindingFormResult is the binding form of a call: each argument's scope and the names it binds.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
type BindingFormResult struct {
	Scopes []manifest.Scope
	Binds  []string
}

// nodeShape is a call's argument nodes as manifest.MatchForm reads them.
type nodeShape []*Node

func (s nodeShape) IsName(i int) bool { return s[i].T == NodeVar && !s[i].Grouped }
func (s nodeShape) IsText(i int) bool { return s[i].T == NodeText }

// sortRoles is which argument of a sort-family call is the binder, the key, the
// direction and the count, from the manifest's forms (-1 where there is none).
func sortRoles(name string, nodes []*Node) manifest.SortRoles {
	r, _ := manifest.Sort(utf8.AsciiUpper(name), len(nodes), nodeShape(nodes))
	return r
}

// BindingForm decodes the binding form of a call from the builtin manifest.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func BindingForm(name string, args []*Node, spec *Spec) *BindingFormResult {
	key := utf8.AsciiUpper(name)
	_, ok := manifest.BindingForms[key]
	if !ok {
		if spec == nil {
			spec = lookup(key)
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

	if f := manifest.MatchForm(key, len(args), nodeShape(args)); f != nil {
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

// HostArity is the argument range of a function registered with RegisterFunction, and false for any other name.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
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

// MayHaveEffects is the effects classification every analysis asks (SPEC §8.1):
// whether a call to name may keep, read or change values beyond its result, so
// that no copy may be left out around it and nothing may be evaluated out of
// order across it. Only a shipped builtin -- a name in spec/builtins.json -- is
// assumed not to. Every other function is the application's, however it was
// installed: RegisterFunction, or a Define outside the manifest, strict, lazy or
// binding. Defining a function below the public API is not a declaration that it
// is pure. The name is enough: Define refuses a second definition and
// RegisterFunction a builtin's name, so a manifest name is always the definition
// the library made at startup.
//
// Registration is a separate question -- HostArity and RegisterFunction's
// replacement rule -- answered by hostFuncs alone.
// For the SQL layer and the tools; see "The syntax tree" in the package documentation.
func MayHaveEffects(name string) bool {
	_, shipped := manifest.Builtins[utf8.AsciiUpper(name)]
	return !shipped
}

// subtreeIsPure reports that evaluating the subtree twice, or not at all, cannot
// be observed and changes no value: it holds no assignment and calls only
// shipped builtins. Iterative, because a flat chain as long as the source can be
// is as deep as it is long; the stack starts in a fixed buffer, so a small
// subtree costs no allocation.
func subtreeIsPure(root *Node) bool {
	var buf [32]*Node
	stack := append(buf[:0], root)
	for len(stack) > 0 {
		n := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		if n == nil {
			continue
		}
		switch n.T {
		case NodeAssign:
			return false
		case NodeCall:
			if MayHaveEffects(n.S) {
				return false
			}
		}
		if n.L != nil {
			stack = append(stack, n.L)
		}
		if n.R != nil {
			stack = append(stack, n.R)
		}
		stack = append(stack, n.Items...)
	}
	return true
}
