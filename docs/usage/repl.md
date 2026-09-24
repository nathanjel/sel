# A REPL in thirty lines

The smallest useful SEL program: read a line, run it, print the result, keep
the variables for the next line. It uses nearly the whole host API — compile,
run against a context that persists, `dependencies()`, and errors with their
codes and positions — and nothing else.

Two commands besides SEL itself: `:deps <expression>` lists what an expression
reads, and `:reset` empties the context.

<!-- tabs -->
<details open>
<summary>Python</summary>

<!-- from: examples/repl/python.py#repl -->
```python
def show(value):
    if value.is_bool():
        return 'TRUE' if value.as_bool() else 'FALSE'
    if value.is_null():
        return 'NULL'
    if value.size() > 0 or value.is_bin():
        return value.dump()
    return value.as_text()


context = Value.none()
for line in sys.stdin:
    line = line.rstrip('\n')
    if line.strip() == '':
        continue
    print('sel>', line)
    try:
        if line == ':reset':
            context = Value.none()
        elif line.startswith(':deps '):
            print(' '.join(compile(line[6:]).dependencies()))
        else:
            print(show(compile(line).run(context)))
    except SelError as e:
        print(f'{e.code} at {e.line}:{e.col}')
```

</details>
<details>
<summary>JavaScript</summary>

<!-- from: examples/repl/js.mjs#repl -->
```js
function show(value) {
  if (value.isBool()) return value.asBool() ? 'TRUE' : 'FALSE';
  if (value.isNull()) return 'NULL';
  if (value.size() > 0 || value.isBin()) return value.dump();
  return value.asText();
}

let context = Value.none();
for await (const line of createInterface({ input: process.stdin })) {
  if (line.trim() === '') continue;
  console.log('sel>', line);
  try {
    if (line === ':reset') {
      context = Value.none();
    } else if (line.startsWith(':deps ')) {
      console.log(compile(line.slice(6)).dependencies().join(' '));
    } else {
      console.log(show(compile(line).run(context)));
    }
  } catch (e) {
    if (!(e instanceof SelError)) throw e;
    console.log(`${e.code} at ${e.line}:${e.col}`);
  }
}
```

</details>
<details>
<summary>PHP</summary>

<!-- from: examples/repl/php.php#repl -->
```php
function show(Value $value): string
{
    if ($value->isBool()) {
        return $value->asBool() ? 'TRUE' : 'FALSE';
    }
    if ($value->isNull()) {
        return 'NULL';
    }
    if ($value->size() > 0 || $value->isBin()) {
        return $value->dump();
    }
    return $value->asText();
}

$context = Value::none();
while (($line = fgets(STDIN)) !== false) {
    $line = rtrim($line, "\n");
    if (trim($line) === '') {
        continue;
    }
    echo 'sel> ', $line, "\n";
    try {
        if ($line === ':reset') {
            $context = Value::none();
        } elseif (str_starts_with($line, ':deps ')) {
            echo implode(' ', Sel::compile(substr($line, 6))->dependencies()), "\n";
        } else {
            echo show(Sel::compile($line)->run($context)), "\n";
        }
    } catch (SelError $e) {
        echo "{$e->code} at {$e->line}:{$e->col}\n";
    }
}
```

</details>
<details>
<summary>C++</summary>

<!-- from: examples/repl/cpp.cpp#repl -->
```cpp
std::string show(const sel::Value& value) {
  if (value.is_bool()) return value.as_bool() ? "TRUE" : "FALSE";
  if (value.is_null()) return "NULL";
  if (value.size() > 0 || value.is_bin()) return value.dump();
  return value.as_text();
}

int main() {
  sel::Value context = sel::Value::none();
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.find_first_not_of(" \t\r\f\v") == std::string::npos) continue;
    std::cout << "sel> " << line << "\n";
    try {
      if (line == ":reset") {
        context = sel::Value::none();
      } else if (line.starts_with(":deps ")) {
        std::cout << join(sel::compile(line.substr(6)).dependencies(), " ") << "\n";
      } else {
        std::cout << show(sel::compile(line).run(context)) << "\n";
      }
    } catch (const sel::SelError& e) {
      std::cout << e.code() << " at " << e.line() << ":" << e.col() << "\n";
    }
  }
  return 0;
}
```

</details>
<details>
<summary>Common Lisp</summary>

<!-- from: examples/repl/lisp.lisp#repl -->
```lisp
(defun show (value)
  (cond ((sel:value-bool-p value) (if (sel:as-bool value) "TRUE" "FALSE"))
        ((sel:value-null-p value) "NULL")
        ((or (plusp (sel:value-size value)) (sel:value-bin-p value)) (sel:value-dump value))
        (t (sel:as-text value))))

(defun blank-p (line)
  (every (lambda (c) (member c '(#\Space #\Tab #\Return #\Page))) line))

(defun main ()
  (let ((context (sel:make-none)))
    (loop for line = (read-line *standard-input* nil)
          while line
          unless (blank-p line)
            do (format t "sel> ~a~%" line)
               (handler-case
                   (cond ((string= line ":reset")
                          (setf context (sel:make-none)))
                         ((and (>= (length line) 6) (string= ":deps " line :end2 6))
                          (format t "~{~a~^ ~}~%"
                                  (sel:dependencies (sel:compile-source (subseq line 6)))))
                         (t
                          (format t "~a~%" (show (sel:run (sel:compile-source line) context)))))
                 (sel:sel-error (e)
                   (format t "~a at ~D:~D~%"
                           (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
  0)
```

</details>
<!-- /tabs -->

Four details every version shares, because the five print the same bytes:

- **One context lives across lines**, so `A = (1, 2, 3)` on one line is `A` on
  the next. `:reset` replaces it with an empty one.
- **A result is shown in SEL's own spelling**: `TRUE`/`FALSE` rather than the
  host's `true`, `1` or `T`; a structure as its dump; text as it is.
- **An error prints its code and position, not its message.** The code and the
  position are part of the language's contract and identical everywhere; the
  message is human text that may be worded differently.
- **Compiling is separate from running** — `IF(1, "a", "b")` compiles and then
  fails when run, and `MISSING + 1` compiles and then finds nothing to read.

## A session

Fed [`session.txt`](../../examples/repl/session.txt), every version prints:

<!-- from: examples/repl/output.txt -->
```text
sel> 2.50 + 2.50
5.00
sel> A = (1, 2, 3)
-{"1"=t"1", "2"=t"2", "3"=t"3"}
sel> SUM(A, _ * 10)
60
sel> "total: {SUM(A, _)}"
total: 6
sel> A .> MAP(_ * _) .> JOIN(", ")
1, 4, 9
sel> 1 / 3
0.3333333333
sel> A[2] == 2
TRUE
sel> IF(1, "a", "b")
E_NOT_BOOL at 1:4
sel> MISSING + 1
E_UNDEF_VAR at 1:1
sel> :deps QTY * PRICE > LIMIT
LIMIT PRICE QTY
sel> :reset
sel> A
E_UNDEF_VAR at 1:1
sel> RMATCH('^\d{2}-\d{3}$', "31-874")
TRUE
```

The project's own command-line tools — `node js/bin/sel.mjs`, `php php/bin/sel`,
`cpp/build/sel`, `lisp/bin/sel`, `python3 -m sel` — are this loop with line
editing, `-e 'expr'` for one-shot use and `--deps` for dependencies.
