# Your own functions, in SQL

An application's own functions ([Scripting](scripting.md)) run in memory. When the
same computation exists in the database — a built-in function, an extension's, or
one the application created — the application can give its function a **SQL
spelling** for that dialect. Rules and pipelines that call the function then
translate like any other, and the planner can push the steps that call it into the
database.

Two implementations of one function is a promise, and it is the application's:
**the spelling must compute what the local function computes**, for every value a
rule can pass it. SEL checks the shape — the arity, the kind of each argument, a
scalar result — and cannot check the meaning, so every fragment that uses a
spelling says so with the caveat `host-function`, and strict translation refuses
it. The example below checks the promise the only way it can be checked: by running
each pipeline both ways over the same data and comparing.

- [The functions](#the-functions)
- [The database side](#the-database-side)
- [Registering, then spelling](#registering-then-spelling)
- [The pipelines](#the-pipelines)
- [What it prints](#what-it-prints)
- [The rules](#the-rules)
- [When the two disagree](#when-the-two-disagree)

---

## The functions

| Function | Shape | Local implementation | SQL spelling (PostgreSQL) |
|---|---|---|---|
| `SLUG(title)` | text → text, a plain mapping | host code | `slug()`, an SQL function |
| `MARGIN_PCT(price, cost)` | two numbers → a number | a SEL expression, for exact decimals | `margin_pct()`, an SQL function |
| `VAT_RATE(country, category)` | a lookup in a table | a copy of `vat_rates` loaded at start | `vat_rate()`, reads `vat_rates` |
| `SHIPPING_COST(kg, country)` | logic with tiers | a SEL expression | `shipping_cost()`, PL/pgSQL |
| `HAS_TAG(tags, tag)` | takes a list | host code | an inline `tag = ANY(ARRAY[…])` |
| `WORDS(title)` | returns a list | host code | none: a list has no SQL value |

## The database side

The seed ([`seed.postgresql.sql`](../../examples/sql-functions/seed.postgresql.sql))
creates the shop's tables and four functions. Two are one-line SQL functions,
one reads a table, and one is PL/pgSQL:

```sql
CREATE FUNCTION slug(t text) RETURNS text IMMUTABLE LANGUAGE sql AS $$
  SELECT trim(both '-' from regexp_replace(lower(t), '[^a-z0-9]+', '-', 'g'))
$$;

CREATE FUNCTION vat_rate(c text, k text) RETURNS numeric STABLE LANGUAGE sql AS $$
  SELECT coalesce(
    (SELECT rate FROM vat_rates WHERE country = c AND category = k),
    (SELECT rate FROM vat_rates WHERE country = c AND category = '*'),
    0)
$$;

CREATE FUNCTION shipping_cost(weight numeric, c text) RETURNS numeric IMMUTABLE LANGUAGE plpgsql AS $$
DECLARE
  base numeric;
BEGIN
  IF weight <= 1 THEN base := 4.90;
  ELSIF weight <= 5 THEN base := 9.90;
  ELSIF weight <= 20 THEN base := 19.90;
  ELSE base := 49.00;
  END IF;
  IF c <> 'PL' THEN
    RETURN base * 2;
  END IF;
  RETURN base;
END
$$;
```

A spelling can call a PostgreSQL `FUNCTION`, not a `PROCEDURE`: a procedure cannot
appear in an expression. `IMMUTABLE` and `STABLE` let PostgreSQL plan the call like
any other expression; SEL does not need them, the database does.

## Registering, then spelling

The local implementations are ordinary host functions. Where the database does
exact decimal arithmetic, the local side does too, by running a SEL expression —
`MARGIN_PCT` and `SHIPPING_COST` are one line of SEL each — so the two agree to
the last digit rather than to a float's precision:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-functions/python.py#local -->
```python
def slug(args):
    out, dash = [], False
    for c in args.text(0):
        c = chr(ord(c) + 32) if 'A' <= c <= 'Z' else c          # ASCII only, as SQL's
        if 'a' <= c <= 'z' or '0' <= c <= '9':                  # [^a-z0-9]+ sees it
            if dash and out:
                out.append('-')
            out.append(c)
            dash = False
        else:
            dash = True
    return Value.text(''.join(out))


MARGIN = compile('ROUND((PRICE - COST) * 100 / PRICE, 1)')


def margin_pct(args):
    ctx = Value.none()
    ctx.set('PRICE', args.val(0))
    ctx.set('COST', args.val(1))
    return MARGIN.run(ctx)


RATES = {(r.get('country').as_text(), r.get('category').as_text()): r.get('rate')
         for r in query(conn, 'SELECT * FROM vat_rates').values()}


def vat_rate(args):
    country, category = args.text(0), args.text(1)
    rate = RATES.get((country, category))
    if rate is None:                          # not `or`: a Value with no children is falsy
        rate = RATES.get((country, '*'))
    return rate.clone() if rate is not None else Value.text('0')


SHIPPING = compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
                   ' * IF(COUNTRY $== "PL", 1, 2)')


def shipping_cost(args):
    ctx = Value.none()
    ctx.set('KG', args.val(0))
    ctx.set('COUNTRY', args.val(1))
    return SHIPPING.run(ctx)


def has_tag(args):
    tags, tag = args.val(0), args.text(1)
    values = tags.values() if tags.size() > 0 else [tags]      # a scalar is a list of one
    return Value.bool(any(v.as_text() == tag for v in values))


def words(args):
    out = Value.none()
    for w in slug(args).as_text().split('-'):
        if w:
            out.set(str(out.size() + 1), Value.text(w))
    return out


register_function('SLUG', 1, 1, slug)
register_function('MARGIN_PCT', 2, 2, margin_pct)
register_function('VAT_RATE', 2, 2, vat_rate)
register_function('SHIPPING_COST', 2, 2, shipping_cost)
register_function('HAS_TAG', 2, 2, has_tag)
register_function('WORDS', 1, 1, words)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-functions/js.mjs#local -->
```js
function slug(args) {
  const out = [];
  let dash = false;
  for (let c of args.text(0)) {
    if (c >= 'A' && c <= 'Z') c = String.fromCharCode(c.charCodeAt(0) + 32);  // ASCII only, as SQL's
    if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {                   // [^a-z0-9]+ sees it
      if (dash && out.length > 0) out.push('-');
      out.push(c);
      dash = false;
    } else {
      dash = true;
    }
  }
  return Value.text(out.join(''));
}

const MARGIN = compile('ROUND((PRICE - COST) * 100 / PRICE, 1)');

function marginPct(args) {
  const ctx = Value.none();
  ctx.set('PRICE', args.val(0));
  ctx.set('COST', args.val(1));
  return MARGIN.run(ctx);
}

const RATES = new Map();
for (const r of (await query(conn, 'SELECT * FROM vat_rates')).values()) {
  RATES.set(`${r.get('country').asText()}/${r.get('category').asText()}`, r.get('rate'));
}

function vatRate(args) {
  const country = args.text(0), category = args.text(1);
  const rate = RATES.get(`${country}/${category}`) ?? RATES.get(`${country}/*`);
  return rate !== undefined ? rate.clone() : Value.text('0');
}

const SHIPPING = compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
  + ' * IF(COUNTRY $== "PL", 1, 2)');

function shippingCost(args) {
  const ctx = Value.none();
  ctx.set('KG', args.val(0));
  ctx.set('COUNTRY', args.val(1));
  return SHIPPING.run(ctx);
}

function hasTag(args) {
  const tags = args.val(0), tag = args.text(1);
  const values = tags.size() > 0 ? tags.values() : [tags];      // a scalar is a list of one
  return Value.bool(values.some((v) => v.asText() === tag));
}

function words(args) {
  const out = Value.none();
  for (const w of slug(args).asText().split('-')) {
    if (w !== '') out.set(String(out.size() + 1), Value.text(w));
  }
  return out;
}

registerFunction('SLUG', 1, 1, slug);
registerFunction('MARGIN_PCT', 2, 2, marginPct);
registerFunction('VAT_RATE', 2, 2, vatRate);
registerFunction('SHIPPING_COST', 2, 2, shippingCost);
registerFunction('HAS_TAG', 2, 2, hasTag);
registerFunction('WORDS', 1, 1, words);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-functions/php.php#local -->
```php
$slug = function (Args $args): Value {
    $out = '';
    $dash = false;
    foreach (str_split($args->text(0)) as $c) {
        $o = ord($c);
        $o = $o >= 0x41 && $o <= 0x5A ? $o + 32 : $o;            // bytes, ASCII only, as SQL's
        if (($o >= 0x61 && $o <= 0x7A) || ($o >= 0x30 && $o <= 0x39)) {   // [^a-z0-9]+ sees it
            if ($dash && $out !== '') {
                $out .= '-';
            }
            $out .= chr($o);
            $dash = false;
        } else {
            $dash = true;
        }
    }
    return Value::text($out);
};

$margin = Sel::compile('ROUND((PRICE - COST) * 100 / PRICE, 1)');

$marginPct = function (Args $args) use ($margin): Value {
    $ctx = Value::none();
    $ctx->set('PRICE', $args->val(0));
    $ctx->set('COST', $args->val(1));
    return $margin->run($ctx);
};

$rates = [];
foreach (query($conn, 'SELECT * FROM vat_rates')->values() as $r) {
    $rates[$r->get('country')->asText()][$r->get('category')->asText()] = $r->get('rate');
}

$vatRate = function (Args $args) use ($rates): Value {
    [$country, $category] = [$args->text(0), $args->text(1)];
    // ?? tests for null, not truthiness, so a rate with no children still counts
    $rate = $rates[$country][$category] ?? $rates[$country]['*'] ?? null;
    return $rate !== null ? $rate->copy() : Value::text('0');
};

$shipping = Sel::compile('COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)'
                         . ' * IF(COUNTRY $== "PL", 1, 2)');

$shippingCost = function (Args $args) use ($shipping): Value {
    $ctx = Value::none();
    $ctx->set('KG', $args->val(0));
    $ctx->set('COUNTRY', $args->val(1));
    return $shipping->run($ctx);
};

$hasTag = function (Args $args): Value {
    [$tags, $tag] = [$args->val(0), $args->text(1)];
    $values = $tags->size() > 0 ? $tags->values() : [$tags];   // a scalar is a list of one
    foreach ($values as $v) {
        if ($v->asText() === $tag) {
            return Value::bool(true);
        }
    }
    return Value::bool(false);
};

$words = function (Args $args) use ($slug): Value {
    $out = Value::none();
    foreach (explode('-', $slug($args)->asText()) as $w) {
        if ($w !== '') {
            $out->set((string) ($out->size() + 1), Value::text($w));
        }
    }
    return $out;
};

Sel::registerFunction('SLUG', 1, 1, $slug);
Sel::registerFunction('MARGIN_PCT', 2, 2, $marginPct);
Sel::registerFunction('VAT_RATE', 2, 2, $vatRate);
Sel::registerFunction('SHIPPING_COST', 2, 2, $shippingCost);
Sel::registerFunction('HAS_TAG', 2, 2, $hasTag);
Sel::registerFunction('WORDS', 1, 1, $words);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-functions/cpp.cpp#local -->
```cpp
const auto slug = [](const std::string& text) {
  std::string out;
  bool dash = false;
  for (char c : text) {
    if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);     // ASCII only, as SQL's
    if ((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9')) {       // [^a-z0-9]+ sees it
      if (dash && !out.empty()) out += '-';
      out += c;
      dash = false;
    } else {
      dash = true;
    }
  }
  return out;
};

const sel::Program margin = sel::compile("ROUND((PRICE - COST) * 100 / PRICE, 1)");

const sel::Value vat_rates = db::query(conn, "SELECT * FROM vat_rates");
std::map<std::pair<std::string, std::string>, sel::Value> rates;
for (const auto& [n, r] : vat_rates.entries())
  rates[{r.get("country")->as_text(), r.get("category")->as_text()}] = *r.get("rate");

const sel::Program shipping = sel::compile(
    "COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)"
    " * IF(COUNTRY $== \"PL\", 1, 2)");

sel::register_function("SLUG", 1, 1, [&](sel::HostArgs& args) {
  return sel::Value::text(slug(args.text(0)));
});
sel::register_function("MARGIN_PCT", 2, 2, [&](sel::HostArgs& args) {
  sel::Value ctx = sel::Value::none();
  ctx.set("PRICE", args.val(0));
  ctx.set("COST", args.val(1));
  return margin.run(ctx);
});
sel::register_function("VAT_RATE", 2, 2, [&](sel::HostArgs& args) {
  const std::string country = args.text(0), category = args.text(1);
  auto rate = rates.find({country, category});
  if (rate == rates.end()) rate = rates.find({country, "*"});   // find(), never the Value's truth
  return rate != rates.end() ? rate->second.clone() : sel::Value::text("0");
});
sel::register_function("SHIPPING_COST", 2, 2, [&](sel::HostArgs& args) {
  sel::Value ctx = sel::Value::none();
  ctx.set("KG", args.val(0));
  ctx.set("COUNTRY", args.val(1));
  return shipping.run(ctx);
});
sel::register_function("HAS_TAG", 2, 2, [](sel::HostArgs& args) {
  const sel::Value& tags = args.val(0);
  const std::string tag = args.text(1);
  if (tags.size() == 0) return sel::Value::boolean(tags.as_text() == tag);   // a scalar is a list of one
  for (const auto& [key, v] : tags.entries())
    if (v.as_text() == tag) return sel::Value::boolean(true);
  return sel::Value::boolean(false);
});
sel::register_function("WORDS", 1, 1, [&](sel::HostArgs& args) {
  sel::Value out = sel::Value::none();
  std::istringstream words(slug(args.text(0)));
  for (std::string w; std::getline(words, w, '-');)
    if (!w.empty()) out.set(std::to_string(out.size() + 1), sel::Value::text(w));
  return out;
});
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-functions/lisp.lisp#local -->
```lisp
(defun slug (args)
  (let ((out (make-string-output-stream)) (dash nil) (empty t))
    (loop for c across (sel:args-text args 0)
          for lc = (if (char<= #\A c #\Z) (code-char (+ (char-code c) 32)) c) ; ASCII only, as SQL's
          do (cond ((or (char<= #\a lc #\z) (char<= #\0 lc #\9))       ; [^a-z0-9]+ sees it
                    (when (and dash (not empty)) (write-char #\- out))
                    (write-char lc out)
                    (setf dash nil empty nil))
                   (t (setf dash t))))
    (sel:make-text (get-output-stream-string out))))

(defparameter *margin* (sel:compile-source "ROUND((PRICE - COST) * 100 / PRICE, 1)"))

(defun margin-pct (args)
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "PRICE" (sel:args-val args 0))
    (sel:value-set ctx "COST" (sel:args-val args 1))
    (sel:run *margin* ctx)))

(defparameter *rates*
  (let ((rates (make-hash-table :test #'equal)))
    (dolist (r (sel:value-values (sel-db:query *conn* "SELECT * FROM vat_rates")) rates)
      (setf (gethash (list (sel:as-text (sel:value-get r "country"))
                           (sel:as-text (sel:value-get r "category")))
                     rates)
            (sel:value-get r "rate")))))

(defun vat-rate (args)
  (let* ((country (sel:args-text args 0))
         (category (sel:args-text args 1))
         ;; OR is safe here: a missing rate is NIL, and a SEL value never is.
         (rate (or (gethash (list country category) *rates*)
                   (gethash (list country "*") *rates*))))
    (if rate (sel:value-copy rate) (sel:make-text "0"))))

(defparameter *shipping*
  (sel:compile-source
   (concatenate 'string "COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)"
                " * IF(COUNTRY $== \"PL\", 1, 2)")))

(defun shipping-cost (args)
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "KG" (sel:args-val args 0))
    (sel:value-set ctx "COUNTRY" (sel:args-val args 1))
    (sel:run *shipping* ctx)))

(defun has-tag (args)
  (let* ((tags (sel:args-val args 0))
         (tag (sel:args-text args 1))
         (members (if (plusp (sel:value-size tags))
                      (sel:value-values tags)
                      (list tags))))                    ; a scalar is a list of one
    (sel:make-bool (some (lambda (v) (string= (sel:as-text v) tag)) members))))

(defun words (args)
  (let ((out (sel:make-none)))
    (dolist (w (uiop:split-string (sel:as-text (slug args)) :separator "-") out)
      (when (plusp (length w))
        (sel:value-set out (format nil "~D" (1+ (sel:value-size out))) (sel:make-text w))))))

(sel:register-function "SLUG" 1 1 #'slug)
(sel:register-function "MARGIN_PCT" 2 2 #'margin-pct)
(sel:register-function "VAT_RATE" 2 2 #'vat-rate)
(sel:register-function "SHIPPING_COST" 2 2 #'shipping-cost)
(sel:register-function "HAS_TAG" 2 2 #'has-tag)
(sel:register-function "WORDS" 1 1 #'words)
```

</details>
<!-- /tabs -->

Then the spellings, one `define` per function and dialect. The function must be
registered first — a spelling for an unknown name is refused where it is written:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-functions/python.py#spell -->
```python
map.define('postgresql', 'funcs', 'SLUG',
           {'tpl': 'slug({0})', 'ret': 'TEXT', 'args': ['TEXT']})
map.define('postgresql', 'funcs', 'MARGIN_PCT',
           {'tpl': 'margin_pct({0}, {1})', 'ret': 'NUM', 'args': ['NUM', 'NUM']})
map.define('postgresql', 'funcs', 'VAT_RATE',
           {'tpl': 'vat_rate({0}, {1})', 'ret': 'NUM', 'args': ['TEXT', 'TEXT']})
map.define('postgresql', 'funcs', 'SHIPPING_COST',
           {'tpl': 'shipping_cost({0}, {1})', 'ret': 'NUM', 'args': ['NUM', 'TEXT']})
map.define('postgresql', 'funcs', 'HAS_TAG',
           {'tpl': '({1} = ANY(ARRAY[{0}]))', 'ret': 'BOOL', 'args': ['LIST', 'TEXT']})
# WORDS returns a list: no spelling can say that, so it has none.
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-functions/js.mjs#spell -->
```js
map.define('postgresql', 'funcs', 'SLUG',
  { tpl: 'slug({0})', ret: 'TEXT', args: ['TEXT'] });
map.define('postgresql', 'funcs', 'MARGIN_PCT',
  { tpl: 'margin_pct({0}, {1})', ret: 'NUM', args: ['NUM', 'NUM'] });
map.define('postgresql', 'funcs', 'VAT_RATE',
  { tpl: 'vat_rate({0}, {1})', ret: 'NUM', args: ['TEXT', 'TEXT'] });
map.define('postgresql', 'funcs', 'SHIPPING_COST',
  { tpl: 'shipping_cost({0}, {1})', ret: 'NUM', args: ['NUM', 'TEXT'] });
map.define('postgresql', 'funcs', 'HAS_TAG',
  { tpl: '({1} = ANY(ARRAY[{0}]))', ret: 'BOOL', args: ['LIST', 'TEXT'] });
// WORDS returns a list: no spelling can say that, so it has none.
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-functions/php.php#spell -->
```php
Map::define('postgresql', 'funcs', 'SLUG',
    ['tpl' => 'slug({0})', 'ret' => 'TEXT', 'args' => ['TEXT']]);
Map::define('postgresql', 'funcs', 'MARGIN_PCT',
    ['tpl' => 'margin_pct({0}, {1})', 'ret' => 'NUM', 'args' => ['NUM', 'NUM']]);
Map::define('postgresql', 'funcs', 'VAT_RATE',
    ['tpl' => 'vat_rate({0}, {1})', 'ret' => 'NUM', 'args' => ['TEXT', 'TEXT']]);
Map::define('postgresql', 'funcs', 'SHIPPING_COST',
    ['tpl' => 'shipping_cost({0}, {1})', 'ret' => 'NUM', 'args' => ['NUM', 'TEXT']]);
Map::define('postgresql', 'funcs', 'HAS_TAG',
    ['tpl' => '({1} = ANY(ARRAY[{0}]))', 'ret' => 'BOOL', 'args' => ['LIST', 'TEXT']]);
// WORDS returns a list: no spelling can say that, so it has none.
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-functions/cpp.cpp#spell -->
```cpp
Map::define("postgresql", Section::Funcs, "SLUG",
            EntrySpec::tpl("slug({0})", "TEXT").args({"TEXT"}));
Map::define("postgresql", Section::Funcs, "MARGIN_PCT",
            EntrySpec::tpl("margin_pct({0}, {1})", "NUM").args({"NUM", "NUM"}));
Map::define("postgresql", Section::Funcs, "VAT_RATE",
            EntrySpec::tpl("vat_rate({0}, {1})", "NUM").args({"TEXT", "TEXT"}));
Map::define("postgresql", Section::Funcs, "SHIPPING_COST",
            EntrySpec::tpl("shipping_cost({0}, {1})", "NUM").args({"NUM", "TEXT"}));
Map::define("postgresql", Section::Funcs, "HAS_TAG",
            EntrySpec::tpl("({1} = ANY(ARRAY[{0}]))", "BOOL").args({"LIST", "TEXT"}));
// WORDS returns a list: no spelling can say that, so it has none.
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-functions/lisp.lisp#spell -->
```lisp
(sel.sql:define-entry "postgresql" :funcs "SLUG"
                      (list :tpl "slug({0})" :ret "TEXT" :args '("TEXT")))
(sel.sql:define-entry "postgresql" :funcs "MARGIN_PCT"
                      (list :tpl "margin_pct({0}, {1})" :ret "NUM" :args '("NUM" "NUM")))
(sel.sql:define-entry "postgresql" :funcs "VAT_RATE"
                      (list :tpl "vat_rate({0}, {1})" :ret "NUM" :args '("TEXT" "TEXT")))
(sel.sql:define-entry "postgresql" :funcs "SHIPPING_COST"
                      (list :tpl "shipping_cost({0}, {1})" :ret "NUM" :args '("NUM" "TEXT")))
(sel.sql:define-entry "postgresql" :funcs "HAS_TAG"
                      (list :tpl "({1} = ANY(ARRAY[{0}]))" :ret "BOOL" :args '("LIST" "TEXT")))
;; WORDS returns a list: no spelling can say that, so it has none.
```

</details>
<!-- /tabs -->

`args` says what each argument must be, and so how it renders: `NUM` arguments that
are not declared numbers go through the dialect's numeric guard, a `BOOL` or `BIN`
where `TEXT` is declared is refused, and `LIST` expands a list known when
translating — a literal list, a `columns` binding, a list-valued `value` binding —
into its elements, joined with commas, for the template to wrap: `ARRAY[{0}]` here.

## The pipelines

Each is planned for PostgreSQL, run, and compared with the same program in memory:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-functions/python.py#run -->
```python
with open(os.path.join(HERE, file), encoding='utf-8') as fh:
    program = compile(fh.read())
plan = plan_hybrid(program, 'postgresql', SCHEMA)
rows = execute_hybrid(plan, runner(conn), tables if plan.pure_memory else None)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-functions/js.mjs#run -->
```js
const program = compile(readFileSync(new URL(file, import.meta.url), 'utf8'));
const plan = planHybrid(program, 'postgresql', SCHEMA);
const rows = executeHybrid(plan, await runner(conn, plan), plan.pureMemory ? tables : null);
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-functions/php.php#run -->
```php
$program = Sel::compile(file_get_contents(__DIR__ . '/' . $file));
$plan = Sql::planHybrid($program, 'postgresql', $schema);
$rows = Sql::executeHybrid($plan, runner($conn), $plan->pureMemory ? $tables : null);
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-functions/cpp.cpp#run -->
```cpp
const sel::Program program = sel::compile(read("examples/sql-functions/" + file));
const HybridPlan plan = Sql::plan_hybrid(program, "postgresql", schema);
const sel::Value rows = Sql::execute_hybrid(plan, db::runner(conn),
                                            plan.pure_memory ? tables : sel::Value::none());
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-functions/lisp.lisp#run -->
```lisp
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in PostgreSQL as PostgreSQL can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "postgresql" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
```

</details>
<!-- /tabs -->

<!-- from: examples/sql-functions/gifts-by-margin.sel -->
```sel
# Gifts with a margin of 40% or more: a list argument, two stored functions, one statement.
PRODUCTS
  .> FILTER(HAS_TAG((_["tag1"], _["tag2"], _["tag3"]), "gift")
            AND MARGIN_PCT(_["price"], _["cost"]) >= 40)
  .> MAP(RECORD("product", SLUG(_["title"]), "margin", MARGIN_PCT(_["price"], _["cost"])))
  .> SORT_BY(_["margin"], "DESC")
```

<!-- from: examples/sql-functions/gross-per-country.sel -->
```sel
# Gross revenue and shipping per country: stored functions inside joins, arithmetic and a GROUP BY.
LINES
  .> LINK(ORDERS, L, O, L["order_id"] == O["order_id"])
  .> LINK(PRODUCTS, X, P, X["product_id"] == P["product_id"])
  .> MAP(RECORD(
       "country", _["country"],
       "gross", _["qty"] * _["price"] * (1 + VAT_RATE(_["country"], _["category"])),
       "shipping", SHIPPING_COST(_["weight_kg"] * _["qty"], _["country"])))
  .> BUCKET(_["country"], RECORD(
       "country", _K, "gross", SUM(_, _["gross"]), "shipping", SUM(_, _["shipping"])))
  .> SORT_BY(_["gross"], "DESC")
```

<!-- from: examples/sql-functions/title-words.sel -->
```sel
# Words in the titles of the better-margin products: WORDS returns a list, so that pair stays in memory.
PRODUCTS
  .> FILTER(MARGIN_PCT(_["price"], _["cost"]) > 30)
  .> SORT_BY(_["product_id"])
  .> MAP(RECORD("product", SLUG(_["title"]), "words", COUNT(WORDS(_["title"]))))
```

And strict translation, which refuses what only the application vouches for:

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/sql-functions/python.py#strict -->
```python
rule = compile('SLUG(TITLE) $== "cast-iron-pan"')
title = {'TITLE': Binding.column('title', 'p', 'TEXT')}
print('   caveats    ', ', '.join(Sql.translate(rule, 'postgresql', title).caveats))
try:
    Sql.translate(rule, 'postgresql', title, {'strict': True})
except SqlError as e:
    print('   strict     ', e.code)
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/sql-functions/js.mjs#strict -->
```js
const rule = compile('SLUG(TITLE) $== "cast-iron-pan"');
const title = { TITLE: Binding.column('title', 'p', 'TEXT') };
console.log('   caveats    ', Sql.translate(rule, 'postgresql', title).caveats.join(', '));
try {
  Sql.translate(rule, 'postgresql', title, { strict: true });
} catch (e) {
  if (!(e instanceof SqlError)) throw e;
  console.log('   strict     ', e.code);
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/sql-functions/php.php#strict -->
```php
$rule = Sel::compile('SLUG(TITLE) $== "cast-iron-pan"');
$title = ['TITLE' => Binding::column('title', 'p', 'TEXT')];
echo '   caveats     ', implode(', ', Sql::translate($rule, 'postgresql', $title)->caveats), "\n";
try {
    Sql::translate($rule, 'postgresql', $title, ['strict' => true]);
} catch (SqlError $e) {
    echo '   strict      ', $e->code, "\n";
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/sql-functions/cpp.cpp#strict -->
```cpp
const sel::Program rule = sel::compile("SLUG(TITLE) $== \"cast-iron-pan\"");
const Bindings title({{"TITLE", Binding::column("title", "p", SqlKind::Text)}});
std::cout << "   caveats     " << join(Sql::translate(rule, "postgresql", title).caveats(), ", ")
          << "\n";
try {
  Sql::translate(rule, "postgresql", title, sel::sql::Options{.strict = true});
} catch (const SqlError& e) {
  std::cout << "   strict      " << e.code() << "\n";
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/sql-functions/lisp.lisp#strict -->
```lisp
(let ((rule (sel:compile-source "SLUG(TITLE) $== \"cast-iron-pan\""))
      (title (list (cons "TITLE" (sel.sql:binding-column "title" "p" :text)))))
  (format t "   caveats     ~{~a~^, ~}~%"
          (sel.sql:fragment-caveats (sel.sql:translate rule "postgresql" title)))
  (handler-case (sel.sql:translate rule "postgresql" title '(:strict t))
    (sel.sql:sql-error (e)
      (format t "   strict      ~a~%" (sel.sql:sql-error-code e)))))
```

</details>
<!-- /tabs -->

## What it prints

<!-- from: examples/sql-functions/output.txt -->
```text
1. gifts with a margin of 40% or more
   plan        pure_sql
   sql         SELECT "_sub1".* FROM (SELECT slug("p"."title") AS "product", margin_pct("p"."price", "p"."cost") AS "margin" FROM "products" "p" WHERE (('gift' = ANY(ARRAY["p"."tag1", "p"."tag2", "p"."tag3"])) AND (margin_pct("p"."price", "p"."cost") >= 40))) "_sub1" ORDER BY "_sub1"."margin" DESC
   caveats     host-function, text-order
   | product=harbour-lights  margin=46.1
   | product=cast-iron-pan  margin=46.0
   | product=decimal-tales-2nd-edition  margin=44.9
   in memory   same rows
2. gross revenue and shipping per country
   plan        pure_sql
   sql         SELECT CAST("_sub1"."country" AS TEXT) COLLATE "C" AS "country", COALESCE(SUM("_sub1"."gross"), 0) AS "gross", COALESCE(SUM("_sub1"."shipping"), 0) AS "shipping" FROM (SELECT "o"."country" AS "country", (CAST((CAST("l"."qty" AS NUMERIC) * CAST("p"."price" AS NUMERIC)) AS NUMERIC) * CAST((CAST(1 AS NUMERIC) + CAST(vat_rate("o"."country", "p"."category") AS NUMERIC)) AS NUMERIC)) AS "gross", shipping_cost((CAST("p"."weight_kg" AS NUMERIC) * CAST("l"."qty" AS NUMERIC)), "o"."country") AS "shipping" FROM "order_lines" "l" INNER JOIN "orders" "o" ON ("l"."order_id" = "o"."order_id") INNER JOIN "products" "p" ON ("l"."product_id" = "p"."product_id")) "_sub1" GROUP BY CAST("_sub1"."country" AS TEXT) COLLATE "C" ORDER BY COALESCE(SUM("_sub1"."gross"), 0) DESC
   caveats     host-function
   | country=PL  gross=915.059700  shipping=93.40
   | country=DE  gross=636.640100  shipping=79.40
   | country=FR  gross=369.600000  shipping=117.80
   in memory   same rows
3. words in the titles of the better-margin products
   plan        hybrid
   sql         SELECT slug("p"."title") AS "product", "p"."title" AS "title" FROM "products" "p" WHERE (margin_pct("p"."price", "p"."cost") > 30) ORDER BY "p"."product_id" ASC
   caveats     host-function
   | product=cast-iron-pan  words=3
   | product=chef-s-knife-8  words=4
   | product=decimal-tales-2nd-edition  words=4
   | product=rain-barrel-200l  words=3
   | product=harbour-lights  words=2
   | product=cardinal-rules  words=2
   in memory   same rows
4. strict translation
   caveats     host-function
   strict      E_SQL_UNSUPPORTED
```

- **1** is one statement: the list of three tag columns became `ARRAY["p"."tag1",
  "p"."tag2", "p"."tag3"]`, and `slug()` and `margin_pct()` are called in the select
  list and the `WHERE`. The `text-order` caveat is the planner noting that
  `margin`, computed in a derived table, is sorted without a declared kind.
- **2** is one statement too: two joins, `vat_rate()` inside the arithmetic,
  `shipping_cost()` over the line weight, and a `GROUP BY` over the sums — the
  stored functions run inside PostgreSQL's aggregation.
- **3** is `hybrid`: `WORDS` returns a list and has no spelling, so the planner
  keeps that pair in memory and lets PostgreSQL do the rest — the filter through
  `margin_pct()`, the sort, and `slug()` — returning the title for the local pair.

## The rules

The contract, in full, is [sql/MAP.md §4.7](../../sql/MAP.md#47-spelling-a-host-function):

- **Function first, spelling second.** `define` accepts a SEL function or a
  registered host function; anything else is the host's start-up error.
- **Arity is the registration's**, and is recorded with the spelling. Register the
  function again with a different arity and translation refuses the call until
  the spelling is defined for the new one; the same arity keeps it.
- **`reset()` drops spellings**, not functions.
- **The result is a scalar.** `ret` has no list kind; a function returning a list
  stays in memory — give its SQL uses a scalar companion, `WORD_COUNT` beside
  `WORDS`.
- **The builtins' argument rules do not apply**; `args` does (`ANY`, `TEXT`, `NUM`,
  `BOOL`, `BIN`, `LIST`).
- **Per dialect, inherited.** A spelling defined for `postgresql` is used by a
  dialect that extends it, and by nothing else; on MariaDB the same rule is refused
  and runs in memory.
- **Every use carries `host-function`**, and `strict` refuses it.

## When the two disagree

While this example was written, pipeline 2 printed `DIFFERENT`. The database was
right and the local `VAT_RATE` was not: it looked the category's rate up with
`rates.get(...) or rates.get(default)` — and a SEL value with no children is
falsy in Python, so every category-specific rate fell through to the country's
default. Nothing in SEL could have caught that; the comparison did. That is the
practical meaning of "the application promises": test the two implementations
against each other on the data they will see, the way these examples do on every
run.
