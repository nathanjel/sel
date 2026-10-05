package manifest

import "testing"

type shape struct{ names, texts map[int]bool }

func (s shape) IsName(i int) bool { return s.names[i] }
func (s shape) IsText(i int) bool { return s.texts[i] }

func TestSortRoles(t *testing.T) {
	n := func(i ...int) map[int]bool {
		m := map[int]bool{}
		for _, x := range i {
			m[x] = true
		}
		return m
	}
	for _, c := range []struct {
		name  string
		count int
		args  shape
		want  SortRoles
	}{
		{"SORT", 1, shape{}, SortRoles{-1, -1, -1, -1}},
		{"SORT", 2, shape{}, SortRoles{-1, 1, -1, -1}},
		{"SORT", 3, shape{}, SortRoles{1, 2, -1, -1}},
		{"SORT_BY", 2, shape{}, SortRoles{-1, 1, -1, -1}},
		// The text literal last wins over a name in the binder slot.
		{"SORT_BY", 3, shape{names: n(1), texts: n(2)}, SortRoles{-1, 1, 2, -1}},
		{"SORT_BY", 3, shape{names: n(1)}, SortRoles{1, 2, -1, -1}},
		{"SORT_BY", 3, shape{}, SortRoles{-1, 1, 2, -1}},
		{"SORT_BY", 4, shape{}, SortRoles{1, 2, 3, -1}},
		{"TOP", 2, shape{}, SortRoles{-1, -1, -1, 1}},
		{"TOP", 3, shape{}, SortRoles{-1, 1, -1, 2}},
		{"TOP", 4, shape{}, SortRoles{1, 2, -1, 3}},
		{"TOP_BY", 3, shape{}, SortRoles{-1, 1, -1, 2}},
		{"TOP_BY", 4, shape{names: n(1), texts: n(2)}, SortRoles{-1, 1, 2, 3}},
		{"TOP_BY", 4, shape{names: n(1)}, SortRoles{1, 2, -1, 3}},
		{"TOP_BY", 4, shape{}, SortRoles{-1, 1, 2, 3}},
		{"TOP_BY", 5, shape{}, SortRoles{1, 2, 3, 4}},
	} {
		got, ok := Sort(c.name, c.count, c.args)
		if !ok || got != c.want {
			t.Errorf("%s/%d %+v: got %+v %v, want %+v", c.name, c.count, c.args, got, ok, c.want)
		}
	}
}
