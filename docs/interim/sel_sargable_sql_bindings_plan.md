# SEL Implementation Plan: Database-Agnostic Sargable SQL Bindings & Query Plan Optimizations

## Goal Description

A production deployment on an EAV schema (millions of records, MySQL/MariaDB) revealed a critical performance regression: SEL emitted defensive type-casts and binary collations (`CAST(column AS CHAR) COLLATE utf8_bin = CAST(literal AS CHAR) COLLATE utf8_bin`) that invalidated B-Tree indexes, turning fast index seeks into 200,000-row table scans (a 54× to 102× regression). While algebraically correct, this defeated search argumentability (sargability).

This problem is not unique to MySQL:
- **MySQL / MariaDB**: `CAST(col AS CHAR) COLLATE utf8mb4_bin` bypasses index on `(typepath)`.
- **PostgreSQL**: `CAST(col AS TEXT) COLLATE "C"` bypasses index on `(typepath)` unless a functional index `ON tbl ((typepath COLLATE "C"))` exists.
- **SQLite**: `CAST(col AS TEXT)` bypasses index on `(typepath)`.

This plan implements **Category #1 (Simple Moves First)** to solve this across all 5 host languages (Python, JavaScript, PHP, C++23, Common Lisp) and all database dialects (MariaDB, MySQL, PostgreSQL, SQLite, ANSI) in a completely **database-agnostic, tweakable** architecture.

```mermaid
graph TD
    subgraph Host Application / DBA
        Binding["Binding.column('typepath', 'cms_entry', type='TEXT', exact=True)"]
    end

    subgraph SEL SQL Compiler Layer
        Trans["SQL Translator: recognizes exact / sargable metadata"]
        Emit["Emitter: omits redundant CAST and COLLATE"]
    end

    subgraph Database Engines (Optimal Index Seeks)
        MySQL["MariaDB / MySQL: `cms_entry`.`typepath` = 'article' (uses B-Tree ref/range)"]
        PG["PostgreSQL: \"cms_entry\".\"typepath\" = 'article' (uses Index Scan)"]
        SQLite["SQLite: \"cms_entry\".\"typepath\" = 'article' (uses SEARCH TABLE ... USING INDEX)"]
    end

    Binding --> Trans
    Trans --> Emit
    Emit --> MySQL
    Emit --> PG
    Emit --> SQLite
```

---

## User Review Required

> [!IMPORTANT]
> **Key Design Decisions for Review:**
> 1. **Binding API Parameter Names**:
>    We propose extending `Binding.column(...)` and `Binding.raw(...)` across all 5 hosts with:
>    - `exact: bool = False`: Declares that the column already guarantees exact byte comparison semantics in the DB schema (e.g. `COLLATE utf8mb4_bin` in MySQL, `text` in Postgres for equality, `TEXT` in SQLite). When `exact=True`, both `CAST` and `COLLATE` are omitted on text comparisons and `IN` lists.
>    - `sargable: bool = False`: For columns in MySQL/MariaDB with legacy case-insensitive collations (`_ci`), emits the coarse index pre-filter `(col = 'lit' AND exact_residual)`. In PostgreSQL and SQLite, automatically lowers to `col = 'lit'` because their default equality is already exact!
>    - `guard: bool = False`: Forces `numericGuard` (`CASE WHEN ... REGEXP ... THEN CAST ... ELSE NULL END`) even when `type='NUM'`, protecting against dirty/mixed strings in EAV number columns.
> 2. **Sargability for `IN` over Lists**:
>    When `exact=True`, `col IN ("open", "held")` will emit bare `((col = 'open') OR (col = 'held'))` (or `col IN ('open', 'held')`) without per-item casts and collations, restoring index range seeks across all 4 database engines.
> 3. **Preserving Defensive Defaults**:
>    Unannotated columns (`exact=False`, `sargable=False`) will continue emitting the existing defensive casts/collations so that zero existing queries break and semantic correctness is preserved by default.

---

## Open Questions

> [!NOTE]
> - **`exact` vs `collation` naming**: Should we accept `exact: bool = True` or `collation: "binary" | "exact"`?
>   *Recommendation*: Support both `exact: bool = True` as the primary concise boolean flag, and normalize `collation: "binary"` or `collation: "exact"` to `exact=True` for backward/DBA familiarity.
> - **Query Plan Verification in CI**:
>   Should we add `EXPLAIN [ANALYZE]` verification to [`tools/oracle-db.sh`](file:///home/nathan/workspaces/nth-share/sel/tools/oracle-db.sh) to ensure that cases flagged as sargable assert `key: <index>` rather than `type: ALL`?
>   *Recommendation*: Yes, asserting on `EXPLAIN` prevents future index regressions.

---

## Proposed Changes

The changes span the Schema Binding Layer, SQL Translator & Emitter, and Test Harness across all 5 host implementations.

```
                    Proposed Change Distribution
                    ┌─────────────────────────┐
                    │  Binding Metadata Specs │
                    │   (Python/JS/PHP/C++/CL)│
                    └────────────┬────────────┘
         ┌──────────────────────┼──────────────────────┐
         ▼                      ▼                      ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│  SQL Translators │   │   SQL Emitters   │   │ Test Generators  │
│  & IN Operators  │   │  (text_operand)  │   │ & sqlt Fixtures  │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

---

### Component 1: Schema Binding Layer (All 5 Hosts)

Extend `Binding.column` and `Binding.raw` to accept `exact: bool = False`, `sargable: bool = False`, and `guard: bool = False`.

#### [MODIFY] [python/sel/sql/binding.py](file:///home/nathan/workspaces/nth-share/sel/python/sel/sql/binding.py)
```python
@staticmethod
def column(column: Any, table: Any = None, type: Any = 'UNKNOWN',
           exact: bool = False, sargable: bool = False, guard: bool = False) -> 'Binding':
    _check_name('column', column)
    if table is not None:
        _check_name('table', table)
    _check_type(type)
    return Binding({'kind': 'column', 'column': column, 'table': table,
                    'type': type, 'exact': bool(exact), 'sargable': bool(sargable),
                    'guard': bool(guard)})

@staticmethod
def raw(sql: Any, type: Any = 'UNKNOWN',
        exact: bool = False, sargable: bool = False, guard: bool = False) -> 'Binding':
    _check_string('a raw column binding', sql)
    if sql == '':
        raise SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty')
    _check_type(type)
    return Binding({'kind': 'column', 'raw': sql, 'type': type,
                    'exact': bool(exact), 'sargable': bool(sargable),
                    'guard': bool(guard)})
```

#### [MODIFY] [js/src/sql/binding.mjs](file:///home/nathan/workspaces/nth-share/sel/js/src/sql/binding.mjs)
```javascript
static column(column, table = null, type = 'UNKNOWN', exact = false, sargable = false, guard = false) {
  checkName('column', column);
  if (table !== null && table !== undefined) checkName('table', table);
  checkType(type);
  return new Binding({
    kind: 'column', column, table: table ?? null, type,
    exact: Boolean(exact), sargable: Boolean(sargable), guard: Boolean(guard)
  });
}

static raw(sql, type = 'UNKNOWN', exact = false, sargable = false, guard = false) {
  checkString('a raw column binding', sql);
  if (sql === '') throw new SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty');
  checkType(type);
  return new Binding({
    kind: 'column', raw: sql, type,
    exact: Boolean(exact), sargable: Boolean(sargable), guard: Boolean(guard)
  });
}
```

#### [MODIFY] [php/src/Sql/Binding.php](file:///home/nathan/workspaces/nth-share/sel/php/src/Sql/Binding.php)
```php
public static function column($column, $table = null, $type = 'UNKNOWN',
                              bool $exact = false, bool $sargable = false,
                              bool $guard = false): self
{
    self::checkName('column', $column);
    if ($table !== null) {
        self::checkName('table', $table);
    }
    self::checkType($type);
    return new self(['kind' => 'column', 'column' => $column,
                     'table' => $table, 'type' => $type,
                     'exact' => $exact, 'sargable' => $sargable,
                     'guard' => $guard]);
}

public static function raw($sql, $type = 'UNKNOWN',
                           bool $exact = false, bool $sargable = false,
                           bool $guard = false): self
{
    self::checkString('a raw column binding', $sql);
    if ($sql === '') {
        throw new SqlError('E_SQL_BINDING', 'a raw column binding cannot be empty');
    }
    self::checkType($type);
    return new self(['kind' => 'column', 'raw' => $sql, 'type' => $type,
                     'exact' => $exact, 'sargable' => $sargable,
                     'guard' => $guard]);
}
```

#### [MODIFY] [cpp/sel_sql.hpp](file:///home/nathan/workspaces/nth-share/sel/cpp/sel_sql.hpp) & [cpp/sel_sql_binding.cpp](file:///home/nathan/workspaces/nth-share/sel/cpp/sel_sql_binding.cpp)
```cpp
struct ColumnSpec {
  bool is_raw = false;
  std::string raw;
  std::string column;
  std::string table;
  SqlKind type = SqlKind::Unknown;
  bool exact = false;
  bool sargable = false;
  bool guard = false;
};

static Binding column(std::string col, std::optional<std::string> table = std::nullopt,
                      SqlKind type = SqlKind::Unknown, bool exact = false,
                      bool sargable = false, bool guard = false);
static Binding raw(std::string sql, SqlKind type = SqlKind::Unknown,
                   bool exact = false, bool sargable = false, bool guard = false);
```

#### [MODIFY] [lisp/src/sql/binding.lisp](file:///home/nathan/workspaces/nth-share/sel/lisp/src/sql/binding.lisp)
```lisp
(defun binding-column (column &optional table (type :unknown) &key (exact nil) (sargable nil) (guard nil))
  (check-name "column" column)
  (when table (check-name "table" table))
  (check-binding-type type)
  (%binding :column (list :column column :table table :type type
                          :exact (not (null exact))
                          :sargable (not (null sargable))
                          :guard (not (null guard)))))
```

---

### Component 2: SQL Fragment & Emitter Layer (All 5 Hosts)

Track `exact`, `sargable`, and `guard` in `Fragment`. Update `text_operand` and `numeric_operand`.

#### [MODIFY] [python/sel/sql/fragment.py](file:///home/nathan/workspaces/nth-share/sel/python/sel/sql/fragment.py) (and JS, PHP, C++, Lisp)
- Add `exact: bool = False`, `sargable: bool = False`, `guard: bool = False` attributes.
- In `column_ref`:
  ```python
  def column_ref(self, c: dict[str, Any]) -> Fragment:
      sql = str(c['raw']) if c.get('raw') is not None else self.emit.column(c.get('table'), str(c['column']))
      f = Fragment([sql], str(c.get('type') or 'UNKNOWN'), self.dialect)
      f.exact = bool(c.get('exact', False))
      f.sargable = bool(c.get('sargable', False))
      f.guard = bool(c.get('guard', False))
      return f
  ```

#### [MODIFY] [python/sel/sql/emit.py](file:///home/nathan/workspaces/nth-share/sel/python/sel/sql/emit.py) (and JS, PHP, C++, Lisp)
- In `text_operand(self, f)`:
  ```python
  def text_operand(self, f):
      if getattr(f, 'exact', False):
          return f
      cast = self.lex('textCast')
      collate = str(self.lex('textCollate') or '')
      parts = f.parts
      if isinstance(cast, str) and cast != '{0}':
          parts = self.fill(cast, [f])
      if collate != '':
          parts = [*parts, collate]
      return Fragment(parts, 'TEXT', self._dialect, f.params, f.param_kinds, f.caveats)
  ```
- In `numeric_operand(self, f, pos=None)`:
  ```python
  def numeric_operand(self, f, pos=None):
      if f.kind == 'NUM' and not getattr(f, 'guard', False):
          return f
      # Wraps in numericGuard (CASE WHEN REGEXP ... THEN CAST ... ELSE NULL END)
  ```

---

### Component 3: SQL Translator & IN Operator (All 5 Hosts)

#### [MODIFY] [python/sel/sql/translator.py](file:///home/nathan/workspaces/nth-share/sel/python/sel/sql/translator.py) (and JS, PHP, C++, Lisp)
- **Byte Comparisons (`$==`, `$!=`, `$<`, `$<=`, `$>`, `$>=`)**:
  - If `l.exact` is true and `r` is a string literal (or `r.exact` is true):
    Both operands bypass `text_operand` wrapping, producing bare `l = r`!
  - If `l.sargable` (or `r.sargable`) and `op == '$=='`:
    - For **MySQL / MariaDB**: emit `(col = 'lit' AND cast(col...) = cast('lit'...))`
    - For **PostgreSQL / SQLite**: emit bare `col = 'lit'` (as PG and SQLite equality are already exact!).
- **`IN` over Literal Lists (`col IN ("a", "b")`)**:
  - When `raw.exact` is true:
    Emit `((col = 'a') OR (col = 'b'))` bare.
    This restores index range scans (`type: range` in MariaDB/MySQL, `Index Scan` in PG, and `SEARCH TABLE USING INDEX` in SQLite)!

---

### Component 4: Test Suite & Generator

#### [MODIFY] [tools/gen-sql-cases.mjs](file:///home/nathan/workspaces/nth-share/sel/tools/gen-sql-cases.mjs)
- In `bindingCall(b, where)`:
  Decode `exact`, `sargable`, and `guard` from JSON bindings:
  ```javascript
  const exact = b.exact === true || b.collation === 'binary' || b.collation === 'exact';
  const sargable = b.sargable === true;
  const guard = b.guard === true;
  ```
  Emit them into constructor arguments for all 5 host test fixtures.

#### [NEW] [sql/cases/19-sargable-bindings.sqlt](file:///home/nathan/workspaces/nth-share/sel/sql/cases/19-sargable-bindings.sqlt)
Exhaustive test cases covering:
1. `TYPEPATH $== "article/news"` with `exact: true` across MariaDB, MySQL, PostgreSQL, and SQLite.
2. `STATUS IN ("open", "held")` with `exact: true`.
3. `VALUE $== "lit"` with `sargable: true` on MariaDB/MySQL (coarse pre-filter) vs PostgreSQL/SQLite (bare equality).
4. `NUMVAL + 10` with `guard: true` (forcing `numericGuard` on dirty EAV data).

---

## Verification Plan

### Automated Tests
1. **Regenerate and run SQL test cases across all 5 hosts**:
   ```bash
   node tools/gen-sql-cases.mjs
   tools/check.sh
   python3 tools/check-sql-cases.py
   node tools/check-sql-cases.mjs
   php tools/check-sql-cases.php
   cpp/build/sqlt
   ```
2. **Live Docker Database Oracle & Query Plan Verification**:
   ```bash
   tools/oracle-db.sh
   ```
   Add a verification script `tools/check-sargable-plans.sh` running `EXPLAIN`:
   - **MariaDB 11.8 & MySQL 8.4**: Assert `key: idx_typepath` and `type: ref` or `type: range` on sargable queries, not `type: ALL`.
   - **PostgreSQL 17**: Assert `Index Scan` or `Bitmap Index Scan`.
   - **SQLite**: Assert `SEARCH TABLE ... USING INDEX`.

### Manual Verification
- Verify that unannotated bindings (`exact: false`) remain 100% byte-identical in their emitted SQL, ensuring zero regressions for existing users.
