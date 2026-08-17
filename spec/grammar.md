# SEL grammar

Normative. Notation: `{ x }` is zero or more, `[ x ]` is optional, `|` is
alternation, `"x"` is literal. Keywords are case-insensitive.

---

## Tokens

```
ident     = ( letter | "_" ) { letter | digit | "_" }
letter    = "A".."Z" | "a".."z"
digit     = "0".."9"
number    = digit { digit } [ "." digit { digit } ]
text      = '"' { char | escape | interpolation } '"'
          | "'" { rawchar | "''" }              "'"
comment   = "#" { any-but-newline }
```

`ident` is ASCII only and case-insensitive; the canonical internal form is upper
case. Reserved: `TRUE FALSE AND OR NOT XOR EQL IN BAND BOR BXOR`.

Operator tokens, longest match first — this ordering matters, since `$<=` must
not lex as `$<` followed by `=`, nor `<=` as `<` then `=`:

```
$==  $!=  $<=  $>=  $<  $>
==   !=   <=   >=   <   >
+=   -=   *=   /=   %=   &=
+    -    *    /    %    &    =
(    )    [    ]    ,    ;
```

---

## Expressions

Loosest binding first. Each production binds tighter than the one above it.

```
program        = sequence EOF

sequence       = list { ";" list } [ ";" ]

list           = assignment { "," assignment }

assignment     = disjunction [ assign_op assignment ]        (* right assoc *)
assign_op      = "=" | "+=" | "-=" | "*=" | "/=" | "%=" | "&="

disjunction    = exclusive { "OR" exclusive }
exclusive      = conjunction { "XOR" conjunction }
conjunction    = negation { "AND" negation }

negation       = "NOT" negation | comparison

comparison     = bit_or [ compare_op bit_or ]                (* non-assoc *)
compare_op     = "==" | "!=" | "<" | "<=" | ">" | ">="
               | "$==" | "$!=" | "$<" | "$<=" | "$>" | "$>="
               | "EQL" | "IN"

bit_or         = bit_xor { "BOR"  bit_xor }
bit_xor        = bit_and { "BXOR" bit_and }
bit_and        = concat  { "BAND" concat  }

concat         = additive { "&" additive }
additive       = multiplicative { ( "+" | "-" ) multiplicative }
multiplicative = unary { ( "*" | "/" | "%" ) unary }

unary          = "-" unary | postfix

postfix        = primary { "[" sequence "]" }

primary        = number
               | text
               | "TRUE" | "FALSE"
               | ident "(" [ sequence ] ")"
               | ident
               | "(" sequence ")"
```

---

## Notes on the productions

**`comparison` is non-associative.** After parsing one `compare_op`, another in
the same position is `E_SYNTAX`. `a < b < c` is rejected at parse time rather
than producing a runtime `E_NOT_BOOL` that points at the wrong place.

**`negation` sits between `comparison` and `conjunction`.** So `NOT a == b` is
`NOT (a == b)` and `NOT a AND b` is `(NOT a) AND b`. This is deliberately unlike
C-family `!`, where the former would compare a boolean against `b`.

**Assignment targets are validated after parsing, not during.** `assignment`
parses a full `disjunction` on the left; if an `assign_op` follows, that node
must be an identifier followed by zero or more index operations, else
`E_BAD_ASSIGN`. This keeps one production instead of a separate lvalue grammar.

**`(` `)` is only grouping.** There is no list-literal syntax. `(3, 2, 1)` is a
parenthesised `,` expression, and `,` builds a list — so the list falls out of the
operator rather than the grammar. `(a)` is just `a`.

**Call arguments are a full `sequence`, then flattened.** The parser parses the
call's contents as one expression and splits it on top-level `,` nodes. Because
`;` binds looser than `,`, a `;` at the top of a call's contents makes the whole
thing **one** argument:

```
IF(c, a; b, d)        -->  IF applied to  (c, a) ; (b, d)   -->  one argument
IF(c, (a; b), d)      -->  three arguments                        (* what you want *)
```

Sequencing inside an argument needs its own parentheses. This is not a special
case in the parser; it is what the stated precedence already means.

**`[` `]` contains a full `sequence`.** Unusual but harmless, and it avoids a
second expression entry point.

---

## Interpolation

Handled entirely in the lexer, before parsing. Within a `"…"` literal, `{` opens
an interpolation that runs to its matching `}`; braces nest, and a `}` inside a
nested text literal does not close it. The literal is rewritten as a `&` chain:

```
"total: {A + 1}."     -->     "total: " & ( A + 1 ) & "."
```

Empty segments are **kept**: `"{A}"` becomes `"" & A & ""`. Dropping them would
make `"{A}"` yield `A` itself, so a literal could evaluate to a list or a boolean
and skip the checks `&` performs. Two no-op concatenations are the cheaper side of
that trade. The parse tree retains no other evidence that interpolation occurred.

`{}` with an empty body is `E_SYNTAX`.

Raw `'…'` literals are not scanned for interpolation.
