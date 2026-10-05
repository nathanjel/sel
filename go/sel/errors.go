// SEL errors. See spec/errors.md — codes are contract, messages are not.

package sel

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
)

// maxDepth is MAX_DEPTH (spec/limits.json): the nesting the evaluator and the
// value walkers allow.
const maxDepth = limits.MAX_DEPTH

// Pos represents a source position with 1-based line and column (in Unicode code points),
// and 0-based byte offset.
type Pos = utf8.Pos

// SelError is the error type raised by SEL compilation and execution.
// Code is a stable identifier from spec/errors.md.
type SelError struct {
	Code    string
	Message string
	Pos     Pos
}

func (e *SelError) Line() int {
	return e.Pos.Line
}

func (e *SelError) Col() int {
	return e.Pos.Col
}

func (e *SelError) Offset() int {
	return e.Pos.Offset
}

func (e *SelError) Error() string {
	if e.Pos.Line > 0 && e.Pos.Col > 0 {
		return fmt.Sprintf("%s at %d:%d: %s", e.Code, e.Pos.Line, e.Pos.Col, e.Message)
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

// Fail raises a SelError by panic.
func Fail(code string, message string, pos Pos) {
	panic(&SelError{
		Code:    code,
		Message: message,
		Pos:     pos,
	})
}

// isSelPanic reports whether a recovered value is a SEL error. Every probe that
// swallows a failure ("does this coerce?", "can this be folded?") swallows only
// that: a Go runtime error (nil dereference, index out of range) recovered by a
// blanket recover() would be kept as "an error" and hidden from the tests.
func isSelPanic(r any) bool {
	_, ok := r.(*SelError)
	return ok
}

// catchSel runs f and returns the *SelError it panics with, or nil; any other
// panic continues (isSelPanic). It is the one spelling of "turn a SEL failure
// into a value"; the per-row join probes inline the same recover, with
// isSelPanic, to stay free of a call per row.
func catchSel(f func()) (err *SelError) {
	defer func() {
		if r := recover(); r != nil {
			se, ok := r.(*SelError)
			if !ok {
				panic(r)
			}
			err = se
		}
	}()
	f()
	return nil
}

// fail raises a SelError by panic, recovered at the API boundaries (catchSel).
func fail(code string, message string, pos Pos) {
	Fail(code, message, pos)
}
