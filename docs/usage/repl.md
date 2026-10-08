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
    if line.strip(' \t\r\n') == '':
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
<summary>Rust</summary>

<!-- from: examples/repl/rust.rs#repl -->
```rust
fn show(value: &Value) -> Result<String, SelError> {
    let at = Pos::default();
    if value.is_bool() {
        return Ok(if value.as_bool(at)? { "TRUE" } else { "FALSE" }.to_string());
    }
    if value.is_null() {
        return Ok("NULL".to_string());
    }
    if value.size() > 0 || value.is_bin() {
        return value.dump();
    }
    value.as_text(at)
}

// One line: a command, or an expression run against the context. A SEL failure
// comes back as the Err, to be printed where the line was read.
fn respond(line: &str, context: &mut Value) -> Result<(), SelError> {
    if line == ":reset" {
        *context = Value::none();
    } else if let Some(expr) = line.strip_prefix(":deps ") {
        println!("{}", compile(expr)?.dependencies()?.join(" "));
    } else {
        println!("{}", show(&compile(line)?.run(Some(context.clone()))?)?);
    }
    Ok(())
}

fn main() -> Result<(), Box<dyn Error>> {
    let mut context = Value::none();
    for line in io::stdin().lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        println!("sel> {line}");
        if let Err(e) = respond(&line, &mut context) {
            println!("{} at {}:{}", e.code, e.pos.line, e.pos.col);
        }
    }
    Ok(())
}
```

</details>
<details>
<summary>Go</summary>

<!-- from: examples/repl/go.go#repl -->
```go
func show(value *sel.Value) string {
	at := sel.Pos{}
	if value.IsBool() {
		if value.AsBool(at) {
			return "TRUE"
		}
		return "FALSE"
	}
	if value.IsNull() {
		return "NULL"
	}
	if value.Size() > 0 || value.IsBin() {
		return value.Dump()
	}
	return value.AsText(at)
}

// One line: a command, or an expression run against the context. A SEL failure
// comes back as the error, to be printed where the line was read.
func respond(line string, context **sel.Value) (err error) {
	defer func() {
		if r := recover(); r != nil {
			e, ok := r.(*sel.SelError)
			if !ok {
				panic(r)
			}
			err = e
		}
	}()
	if line == ":reset" {
		*context = sel.NewNone()
		return nil
	}
	if expr, ok := strings.CutPrefix(line, ":deps "); ok {
		program, err := sel.Compile(expr)
		if err != nil {
			return err
		}
		fmt.Println(strings.Join(program.Dependencies(), " "))
		return nil
	}
	program, err := sel.Compile(line)
	if err != nil {
		return err
	}
	result, err := program.Run(*context)
	if err != nil {
		return err
	}
	fmt.Println(show(result))
	return nil
}

func main() {
	context := sel.NewNone()
	input := bufio.NewScanner(os.Stdin)
	for input.Scan() {
		line := input.Text()
		if strings.TrimSpace(line) == "" {
			continue
		}
		fmt.Println("sel>", line)
		var e *sel.SelError
		if err := respond(line, &context); errors.As(err, &e) {
			fmt.Printf("%s at %d:%d\n", e.Code, e.Line(), e.Col())
		}
	}
	if err := input.Err(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
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

Four details every version shares, because the seven print the same bytes:

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

## The `sel` command line

The project's own command-line tools — `node js/bin/sel.mjs`, `php php/bin/sel`,
`cpp/build/sel`, `lisp/bin/sel`, `python3 -m sel`, `rust/build/sel`,
`go/build/sel` — are **not** the loop above. They are one program written seven
times, held to the contract below by `tools/check-cli-source.sh`; the last two
install as `cargo install sel-lang` and
`go install github.com/nathanjel/sel/go/bin/sel@v0.10.4`. Where the loop above
is an example of the host API, the CLI is a tool, and the two differ on
purpose: the CLI has no `:deps` or `:reset` commands, prints `NULL` as `-` and
BIN as `bin:<hex>`, and writes errors with their message to standard error.

```text
sel -e EXPR          evaluate EXPR and print the result
sel FILE             evaluate the program in FILE
sel --deps -e EXPR   print the variables EXPR reads, one per line, sorted
sel --deps FILE      the same for a file
sel --functions      print the function table, one name per line
sel --help, sel -h   print the usage text
sel --version        print "sel <version>", e.g. sel 0.10.4
sel                  read programs from standard input, one per line
```

**Results** go to standard output, one line each, in the CLI's rendering:
`TRUE`/`FALSE`; `-` for `NULL`; `bin:` and lower-case hex for BIN (`bin:6162`);
text and numbers bare, as their characters (`0.3333333333`); anything with
children as its dump (`-{"1"=t"1", "2"=t"a"}`). An empty dependency list prints
**nothing** — not an empty line.

**A file** is read as bytes and decoded as UTF-8 with no newline translation
(SPEC §2): a CR is program text, and an invalid byte is `E_UTF8` at its
position.

**Errors and exit status:**

| Situation | Exit | Standard error |
|---|---|---|
| success, including `--help`, `-h`, `--version`, `--functions` | 0 | nothing |
| a compile or run error in `-e` or a file | 1 | `E_CODE at line L column C: message` |
| a file that cannot be read — missing, unreadable, or a directory | 1 | `sel: cannot read PATH…` |
| `-e` with no expression after it | 2 | `sel: -e needs an expression` |
| an option the CLI does not have | 2 | `sel: unknown option OPT` |
| a second operand (`sel -e 1 extra`, `sel a.sel b.sel`; in `sel a.sel -e X` the file is the operand and `X` the extra one) | 2 | `sel: unexpected argument ARG` |

Every refusal is that one line, never a stack trace, and writes nothing to
standard output. Only the code and position of an evaluation error are
contract; the message is human text.

**Standard input** (no operand) is a REPL: one context is kept across lines, so
`A = 1` on one line is `A` on the next. Each line is one program; its result
goes to standard output and its error, as above, to standard error, and the
session goes on; at the end of input the CLI exits 0. The last line needs no
newline. A line is skipped as blank only when it consists solely of
SEL's whitespace — space, TAB, CR and LF; a line holding only a no-break space,
a vertical tab or U+3000 is a program, and fails with `E_SYNTAX`. The `sel> `
prompt is written **only when standard input is a terminal**: on a pipe the
output is the results and nothing else, so

```text
$ printf 'A = 1\nA + 1\n' | sel
1
2
```
