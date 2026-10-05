package sql

import (
	"fmt"

	"github.com/nathanjel/sel/go/sel"
)

type Pos = sel.Pos

// SqlError is raised when an expression or pipeline cannot be translated to SQL,
// or when a translation contract is violated.
type SqlError struct {
	Code    string
	Message string
	Pos     Pos
}

func (e *SqlError) Line() int {
	return e.Pos.Line
}

func (e *SqlError) Col() int {
	return e.Pos.Col
}

func (e *SqlError) Offset() int {
	return e.Pos.Offset
}

func (e *SqlError) Error() string {
	if e.Pos.Line > 0 && e.Pos.Col > 0 {
		return fmt.Sprintf("%s at %d:%d: %s", e.Code, e.Pos.Line, e.Pos.Col, e.Message)
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

// refuse raises a SqlError by panic. Recovered at top-level translation boundaries.
func refuse(code string, message string, pos Pos) {
	panic(&SqlError{
		Code:    code,
		Message: message,
		Pos:     pos,
	})
}
