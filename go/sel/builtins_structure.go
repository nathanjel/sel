// Structure built-in functions: constructors, inspection, projection, and slicing.

package sel

import (
	"reflect"
)

func init() {
	Define(&Spec{
		Name: "COUNT",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewInt(int64(args.Val(0).Size()))
		},
	})

	Define(&Spec{
		Name: "INDEXES",
		Min:  1,
		Max:  1,
		Fn: func(args *Args, ctx *Context) *Value {
			keys := args.Val(0).Keys()
			items := make([]*Value, len(keys))
			for i, k := range keys {
				items[i] = NewText(k)
			}
			return newListOwned(items)
		},
	})

	Define(&Spec{
		Name: "HAS",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			return NewBool(args.Val(0).Has(args.Text(1)))
		},
	})

	Define(&Spec{
		Name: "LIST",
		Min:  0,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			items := make([]*Value, args.Count())
			for i := 0; i < args.Count(); i++ {
				// SPEC §3.4: LIST copies its arguments, like `,`.
				items[i] = args.Val(i).CloneAt(2, args.Pos())
			}
			return newListOwned(items)
		},
	})

	Define(&Spec{
		Name: "RECORD",
		Min:  0,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			count := args.Count()
			if count == 0 {
				return NewNone()
			}
			if shape := args.RecordShape(); shape != nil {
				// Every key is a distinct text literal (the parser prepared the
				// shape): the keys cannot fail or have effects, and dispatch has
				// not evaluated them. Values are evaluated and copied in order
				// (GO-P14).
				values := make([]*Value, count/2)
				for i := 1; i < count; i += 2 {
					// SPEC §3.4: RECORD copies its values, like `,`.
					values[i/2] = args.Val(i).CloneAt(2, args.Pos())
				}
				return newShapedRecord(shape, values)
			}
			keys := make([]string, count/2)
			values := make([]*Value, count/2)
			for i := 0; i < count; i += 2 {
				keys[i/2] = args.Text(i)
				// SPEC §3.4: RECORD copies its values, like `,`.
				values[i/2] = args.Val(i + 1).CloneAt(2, args.Pos())
			}
			shape := args.RecordShape()
			if shape != nil && reflect.DeepEqual(shape.keys, keys) {
				return newShapedRecord(shape, values)
			}
			if uShape := uniqueRecordShape(keys); uShape != nil {
				return newShapedRecord(uShape, values)
			}
			entries := make([]Entry, len(keys))
			for i := range keys {
				entries[i] = Entry{Key: keys[i], Val: values[i]}
			}
			return newRecordFromEntries(entries)
		},
	})

	Define(&Spec{
		Name: "TAKE",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			val := args.Val(0)
			count := int(args.NonNegInt(1))
			if count == 0 || val.IsNull() {
				return newListOwned(nil)
			}
			if val.isList && val.storage != nil {
				if count > len(val.storage) {
					count = len(val.storage)
				}
				// A fresh container (SPEC §3.4): the result must not share A's backing array.
				return NewList(val.storage[:count])
			}
			ents := val.Elements()
			if count > len(ents) {
				count = len(ents)
			}
			items := make([]*Value, count)
			for i := 0; i < count; i++ {
				items[i] = ents[i].Val
			}
			return newListOwned(items)
		},
	})

	Define(&Spec{
		Name: "DROP",
		Min:  2,
		Max:  2,
		Fn: func(args *Args, ctx *Context) *Value {
			val := args.Val(0)
			count := int(args.NonNegInt(1))
			if val.IsNull() {
				return newListOwned(nil)
			}
			if val.isList && val.storage != nil {
				if count > len(val.storage) {
					count = len(val.storage)
				}
				return NewList(val.storage[count:])
			}
			ents := val.Elements()
			if count > len(ents) {
				count = len(ents)
			}
			items := make([]*Value, len(ents)-count)
			for i := count; i < len(ents); i++ {
				items[i-count] = ents[i].Val
			}
			return newListOwned(items)
		},
	})

	Define(&Spec{
		Name: "SELECT_COLS",
		Min:  2,
		Max:  -1,
		Fn: func(args *Args, ctx *Context) *Value {
			val := args.Val(0)
			if val.IsNull() {
				return newListOwned(nil)
			}
			numCols := args.Count() - 1
			columns := make([]string, numCols)
			for i := 0; i < numCols; i++ {
				columns[i] = args.Text(i + 1)
			}

			// Fast path for uniform shaped records in list
			if val.isList && val.storage != nil && len(val.storage) > 0 && val.storage[0].shape != nil {
				sampleShape := val.storage[0].shape
				slots := make([]int, numCols)
				allFound := true
				for i, col := range columns {
					slot, ok := sampleShape.keyMap[col]
					if !ok {
						allFound = false
						break
					}
					slots[i] = slot
				}
				if allFound {
					allUniform := true
					for _, r := range val.storage {
						if r.shape != sampleShape {
							allUniform = false
							break
						}
					}
					if allUniform {
						outShape := uniqueRecordShape(columns)
						if outShape != nil {
							outRows := make([]*Value, len(val.storage))
							for rIdx, r := range val.storage {
								rowVals := make([]*Value, numCols)
								for cIdx, s := range slots {
									rowVals[cIdx] = r.storage[s]
								}
								outRows[rIdx] = newShapedRecord(outShape, rowVals)
							}
							return newListOwned(outRows)
						}
					}
				}
			}

			ents := val.Elements()
			rows := make([]*Value, len(ents))
			for i, entry := range ents {
				row := entry.Val
				var rowEntries []Entry
				for _, col := range columns {
					if row.Has(col) {
						rowEntries = append(rowEntries, Entry{Key: col, Val: row.Get(col)})
					}
				}
				rows[i] = newRecordFromEntries(rowEntries)
			}
			return newListOwned(rows)
		},
	})

	dedupeFn := func(args *Args, ctx *Context) *Value {
		val := args.Val(0)
		if val.IsNull() {
			return newListOwned(nil)
		}
		ents := val.Elements()
		buckets := make(map[uint64][]*Value)
		var out []*Value
		for _, e := range ents {
			item := e.Val
			h := item.StructuralHash()
			bucket := buckets[h]
			found := false
			for _, existing := range bucket {
				if item.Eql(existing, Pos{}) {
					found = true
					break
				}
			}
			if !found {
				buckets[h] = append(bucket, item)
				out = append(out, item)
			}
		}
		return newListOwned(out)
	}

	Define(&Spec{
		Name: "DEDUPE",
		Min:  1,
		Max:  1,
		Fn:   dedupeFn,
	})

	Define(&Spec{
		Name: "DISTINCT",
		Min:  1,
		Max:  1,
		Fn:   dedupeFn,
	})
}
