// SEL errors. See spec/errors.md — codes are contract, messages are not.

package sel

import (
	"fmt"

	"github.com/nathanjel/sel/go/internal/limits"
	"github.com/nathanjel/sel/go/internal/utf8"
)

const (
	MAX_DEPTH       = limits.MAX_DEPTH
	MAX_INT_DIGITS  = limits.MAX_INT_DIGITS
	MAX_FRAC_DIGITS = limits.MAX_FRAC_DIGITS
	DIV_SCALE       = limits.DIV_SCALE
)

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

// fail raises a SelError by panic. Recovered at top-level API boundaries.
func fail(code string, message string, pos Pos) {
	Fail(code, message, pos)
}
