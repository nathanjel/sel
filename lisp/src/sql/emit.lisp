;;;; Everything that turns a value or a template into characters. The one place
;;;; quoting happens, so there is one place to get it right.

(in-package #:sel.sql)

(defun replace-all (haystack needle replacement)
  "A plain subsequence replace. Deliberately NOT cl-ppcre:regex-replace-all,
which reads \\1 and \\& in the replacement as directives -- the
template-substitution trap in a different spelling, and one that would fire on
any dialect template containing a backslash."
  (if (zerop (length needle))
      haystack
      (with-output-to-string (out)
        (let ((start 0))
          (loop for at = (search needle haystack :start2 start)
                while at
                do (write-string haystack out :start start :end at)
                   (write-string replacement out)
                   (setf start (+ at (length needle))))
          (write-string haystack out :start start)))))

(defun slot-index (s)
  "The argument a template slot names, or NIL when it names none.

Slots are 0-based and canonical: {0}, {1}, {0:}. `{01}` and `{1\\n}` are not
slots, and tools/gen-sql-map.mjs already refuses both -- so without this the
shipped map and a runtime-registered template would be read by two different
grammars."
  (when (and (stringp s) (plusp (length s)) (<= (length s) 3)
             (every #'ascii-digit-p s)
             (or (string= s "0") (char/= (char s 0) #\0)))
    (parse-integer s)))

(defun lex-text (dialect key &optional pos)
  "A required lexical string, or a refusal naming the key. Python reaches these
through str(map.lexical(...)), which turns an absent value into the four
characters \"None\" and emits them into the query; every dialect supplies the
keys that matter, so neither host has ever done it, but that is not a thing to
leave a query generator resting on."
  (let ((v (dialect-lexical dialect key)))
    (unless (stringp v)
      (refuse "E_SQL_UNSUPPORTED"
              (format nil "dialect ~a has no ~a, which this expression needs to ~
be written at all" dialect key)
              pos))
    v))

;;; --- literals -------------------------------------------------------------

(defun numeric-literal (dialect v pos)
  "The only unquoted output in the layer, and therefore the one thing that has
to be a number.

What is emitted is what the parse RECOVERED, not the text the caller supplied.
The two agree for everything the parser produces, and the difference is the
point: proving a string is a number and then emitting a DIFFERENT string is a
gap, however small, and the gap is where \"1 OR 1=1\" lived."
  (let* ((text (sel:as-text v pos))
         (n (handler-case (sel:as-text (sel:make-num text))
              (sel:sel-error (e)
                ;; Only "that is not a number" becomes a binding refusal.
                ;; E_RANGE -- a numeral past spec/SPEC.md §6.4's cap -- is the
                ;; EVALUATOR's answer about the value itself, and Python lets it
                ;; out the same way.
                (unless (equal (sel:sel-error-code e) "E_NOT_NUM") (error e))
                (refuse "E_SQL_BINDING"
                        (format nil "a value bound as NUM must be a number, and ~
~s is not" text)
                        pos))))
         (wrap (dialect-lexical dialect "numericLiteral")))
    ;; How the dialect spells a number is the dialect's business, and one of
    ;; them has to spell it as text: SQLite has no exact decimal, so 2.50 is a
    ;; REAL that prints as 2.5 and `2.50 $== 2.5` would be TRUE there and FALSE
    ;; in SEL. Applied AFTER canonicalisation, so the digits-by-construction
    ;; guarantee is unaffected.
    (cond ((and (stringp wrap) (not (equal wrap "{0}"))) (replace-all wrap "{0}" n))
          ;; A negative number is parenthesised so a unary minus in front of it
          ;; cannot produce `--`: MariaDB reads that as double negation and gets
          ;; the right answer by luck, while PostgreSQL and SQLite read it as
          ;; the start of a line comment and the rest of the expression
          ;; disappears.
          ((and (plusp (length n)) (char= (char n 0) #\-)) (format nil "(~a)" n))
          (t n))))

(defstruct (escape-plan (:constructor %make-escape-plan (rules ascii other)))
  ;; RULES: the dialect's escape rules, longest key first. ASCII / OTHER: when every
  ;; key is one character, the replacement by character code (a 128-entry vector and
  ;; a table for the rest); both NIL when some key is longer and the rules must be
  ;; tried at every position.
  rules ascii other)

(defvar *escape-plans* (make-hash-table :test #'eq :weakness :key :synchronized t)
  "ESCAPE-PLAN per textEscape rule list, by the identity of the list the dialect's
lexical table returns: the table (and so the list) is dropped whenever a dialect is
registered or reset, which drops the plan with it.")

(defun escape-plan-for (escape)
  (or (gethash escape *escape-plans*)
      (setf (gethash escape *escape-plans*)
            ;; Longest first, so a rule for "\\\\" is applied before one for "\\".
            (let ((rules (stable-sort (copy-list escape) #'> :key (lambda (c) (length (car c))))))
              (if (every (lambda (r) (= (length (car r)) 1)) rules)
                  (let ((ascii (make-array 128 :initial-element nil))
                        (other (make-hash-table)))
                    (dolist (r (reverse rules))
                      (let ((code (char-code (char (car r) 0))))
                        (if (< code 128)
                            (setf (svref ascii code) (cdr r))
                            (setf (gethash code other) (cdr r)))))
                    (%make-escape-plan rules ascii other))
                  (%make-escape-plan rules nil nil))))))

(defun emit-text-literal (dialect text)
  (let ((quote (lex-text dialect "textQuote"))
        (escape (dialect-lexical dialect "textEscape")))
    (if (not (and escape (listp escape)))
        ;; CHECK-QUOTE-PAIRING (map.lisp) refuses a dialect whose textQuote has
        ;; no escape rule, so this cannot be reached; if it were, quoting TEXT
        ;; without escaping it would be an injection, so it refuses.
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "dialect ~a has no textEscape, so a text literal cannot be quoted safely"
                        dialect))
        ;; A single left-to-right pass, never one replace per rule: replacing '
        ;; with '' and then \ with \\ would rewrite the output of the first.
        (let* ((plan (escape-plan-for escape))
               (ascii (escape-plan-ascii plan))
               (rules (escape-plan-rules plan)))
          (with-output-to-string (out)
            (write-string quote out)
            (let ((i 0) (n (length text)))
              (if ascii
                  ;; One character per rule: copy the runs between special
                  ;; characters in one WRITE-STRING each.
                  (let ((other (escape-plan-other plan))
                        (run 0))
                    (loop while (< i n)
                          do (let* ((code (char-code (char text i)))
                                    (rep (if (< code 128)
                                             (svref ascii code)
                                             (gethash code other))))
                               (if rep
                                   (progn (when (< run i) (write-string text out :start run :end i))
                                          (write-string rep out)
                                          (incf i)
                                          (setf run i))
                                   (incf i))))
                    (when (< run n) (write-string text out :start run :end n)))
                  (loop while (< i n)
                        do (let ((hit (find-if (lambda (r)
                                                 (let ((k (car r)))
                                                   (and (plusp (length k))
                                                        (<= (+ i (length k)) n)
                                                        (string= k text :start2 i
                                                                        :end2 (+ i (length k))))))
                                               rules)))
                             (if hit
                                 (progn (write-string (cdr hit) out) (incf i (length (car hit))))
                                 (progn (write-char (char text i) out) (incf i)))))))
            (write-string quote out))))))

(defun emit-literal (dialect v &optional (form :text) pos)
  "A SEL value as a SQL literal, in the form the caller SAYS it has.

The form is passed in and never inferred, because it cannot be inferred: SEL
numbers *are* TEXT values (spec §4), so (make-num \"5.00\") and
(make-text \"5.00\") are the same object and no predicate can tell \"the author
wrote 5.00\" from \"the author wrote \\\"5.00\\\"\". Only the AST knows.

Getting this wrong is not cosmetic. Emitted bare, `\"5.00\" $== \"5\"` becomes
`5.00 = 5`, which the database answers TRUE and SEL answers FALSE."
  (cond
    ((or (eq form :bool) (sel:value-bool-p v))
     (lex-text dialect (if (sel:as-bool v pos) "true" "false") pos))
    ((or (eq form :bin) (sel:value-bin-p v))
     (let ((tpl (dialect-lexical dialect "binaryLiteral")))
       (unless (stringp tpl)
         (refuse "E_SQL_UNSUPPORTED"
                 (format nil "dialect ~a has no binary literal syntax" dialect) pos))
       (replace-all tpl "{hex}" (sel::bytes-to-hex (sel:as-bytes v pos)))))
    ;; A NONE value has no characters, and asking for them raises a SEL-ERROR --
    ;; which TRY-TRANSLATE does not catch, so a host using the refusal-tolerant
    ;; API got a fatal out of AS-VALUE rather than a refusal.
    ((sel:value-none-p v)
     (refuse "E_SQL_BINDING"
             "a value binding holding no value cannot be a SQL literal; only an ~
aggregate can be given an empty binding" pos))
    ((eq form :num) (numeric-literal dialect v pos))
    (t (emit-text-literal dialect (sel:as-text v pos)))))

(defun emit-placeholder (dialect n)
  (let ((tpl (lex-text dialect "placeholder")))
    ;; ~D, not PRINC-TO-STRING: the latter reads the caller's *PRINT-BASE*, so an
    ;; application that had rebound it would get $c where it wanted $12. Python's
    ;; str(int) and PHP's strval have no such knob; this host does.
    (if (search "{n}" tpl) (replace-all tpl "{n}" (format nil "~D" n)) tpl)))

;;; --- identifiers ----------------------------------------------------------

(defun emit-ident (dialect name)
  "A table or column name, quoted. The quote character is doubled -- or whatever
identEscape says -- inside the name, which is what stops a binding naming a
column a\"b from ending the identifier early."
  (let ((q (lex-text dialect "identQuote"))
        (e (lex-text dialect "identEscape")))
    (concatenate 'string q (replace-all name q e) q)))

(defun emit-column (dialect table column)
  (if (or (null table) (equal table ""))
      (emit-ident dialect column)
      (concatenate 'string (emit-ident dialect table) "." (emit-ident dialect column))))

;;; --- templates ------------------------------------------------------------

(defun refuse-no-numeric-guard (dialect pos)
  "DIALECT has no numericGuard: an operand not declared NUM cannot be read as a
number there. Says what the author can act on -- declaring the binding NUM --
rather than naming the missing key."
  (refuse "E_SQL_UNSUPPORTED"
          (format nil "dialect ~a has no way to ask whether a value is a ~
number, so an operand it has not been told is one cannot be read as one here; ~
declare the binding NUM if the column really is numeric" dialect)
          pos))

(defun emit-numeric-operand (dialect f &optional pos)
  "An operand a numeric context will read as a number, made safe to read.

SEL raises E_NOT_NUM for text that is not a number, and the server does not:
CAST('x' AS DECIMAL) is 0 on MariaDB, MySQL and SQLite, so a rule comparing
against 0 matched every row of a text column. Wrapping the operand so a
non-number becomes NULL keeps the warrant -- NULL is not selected, which is what
SEL failing has to look like from SQL.

Not applied to a NUM operand: the binding said it is a number, and that
declaration is where the promise transfers. It is also the only way to keep the
index, since the guard is a function of the column.

The pattern is SEL's own numeral grammar and lives in the map beside funcs.ISNUM,
which asks the same question; tools/gen-sql-map.mjs requires the two to agree. A
dialect that cannot ask it -- sqlite has no REGEXP, ansi has no regex -- declares
no numericGuard, and this refuses rather than emitting something that answers
when SEL would not."
  (if (and (eq (fragment-kind f) :num) (not (fragment-guard f)))
      f
      ;; DIALECT-LEXICAL rather than LEX-TEXT, whose message names the missing
      ;; key. The key is not what an author can act on here; declaring the
      ;; binding NUM is, and that is the sentence this refusal has to say.
      (progn
        (check-numeric-guard dialect)
        (let ((guard (dialect-lexical dialect "numericGuard")))
          (unless (stringp guard)
            (refuse-no-numeric-guard dialect pos))
          (%fragment (emit-fill dialect guard (list f) pos) :num dialect)))))

(defun split-numeric-guard (dialect f &optional pos)
  "The numeric guard's two halves, for a SUM that has to be guarded ALL OR NOTHING
(docs/internals/sql-kinds.md 5a): the TEST that asks whether F is a number, and
the CAST that reads it as one. Both are cut out of the dialect's own numericGuard
template -- `CASE WHEN (<test>) THEN <cast> ELSE NULL END` -- so there is one
spelling of each and a dialect that changes it changes both forms. The TEST keeps
its parentheses. Refuses exactly where EMIT-NUMERIC-OPERAND does; whether the
cast inside the SUM needs a guard of its own is the dialect's guardedSum skeleton
(sql/MAP.md §5.1)."
  (check-numeric-guard dialect)
  (let ((guard (dialect-lexical dialect "numericGuard"))
        (head "CASE WHEN (") (mid ") THEN ") (tail " ELSE NULL END"))
    (unless (stringp guard)
      (refuse-no-numeric-guard dialect pos))
    (let ((m (search mid guard)))
      (unless (and m (eql 0 (search head guard))
                   (eql (- (length guard) (length tail)) (search tail guard :from-end t)))
        (bad "dialect ~a's numericGuard is not CASE WHEN (<test>) THEN <cast> ELSE NULL END, ~
so it cannot be split into the halves an aggregate needs" dialect))
      (values (%fragment (emit-fill dialect (subseq guard (1- (length head)) (1+ m)) (list f) pos)
                         :bool dialect)
              (%fragment (emit-fill dialect
                                    (subseq guard (+ m (length mid)) (- (length guard) (length tail)))
                                    (list f) pos)
                         :num dialect)))))

(defun emit-text-operand (dialect f)
  "An operand of a byte comparison: cast to a character type, then given the
dialect's binary collation.

Both halves are needed and neither is enough alone. Without the collation
MariaDB's default is case-insensitive, so `\"A\" $== \"a\"` is true there and
false in SEL. Without the cast the collation does not stop two numeric operands
being compared as numbers, so `3.0 EQL 3` is true there and false in SEL -- EQL
is structural and does not normalise numbers."
  (if (fragment-exact f)
      f
      (let ((cast (dialect-lexical dialect "textCast"))
            (collate (dialect-lexical dialect "textCollate"))
            (parts (fragment-parts f)))
        (when (and (stringp cast) (not (equal cast "{0}")))
          (setf parts (emit-fill dialect cast (list f))))
        (when (and (stringp collate) (plusp (length collate)))
          (setf parts (append parts (list collate))))
        (%fragment parts :text dialect
                   (fragment-params f)
                   (fragment-param-kinds f)
                   (fragment-caveats f)))))

(defvar *template-segments* (make-hash-table :test #'eq :weakness :key :synchronized t)
  "The parsed form of each mapping template, keyed by the identity of its string: the
map's entries and a dialect's lexical values are stable strings, so each is scanned
once and not at every node it is filled into.")

(defun template-segments (tpl)
  "TPL as a list of segments, in order: a string (literal text, `{{` and `}}` already
unescaped), (:SPLICE k) for {k}, (:JOIN k) for {*} and {k:}, and (:LEXICAL slot) for a
reference to a lexical entry, resolved against the dialect when it is filled."
  (let ((out '()) (i 0) (n (length tpl)))
    (labels ((lit (s) (when (plusp (length s)) (push s out))))
      (loop while (< i n)
            do (let ((c (char tpl i)))
                 (cond
                   ((and (char= c #\{) (< (1+ i) n) (char= (char tpl (1+ i)) #\{))
                    (lit "{") (incf i 2))
                   ((and (char= c #\}) (< (1+ i) n) (char= (char tpl (1+ i)) #\}))
                    (lit "}") (incf i 2))
                   ((char/= c #\{)
                    (let ((j (or (position-if (lambda (ch) (or (char= ch #\{) (char= ch #\})))
                                              tpl :start (1+ i))
                                 n)))
                      (lit (subseq tpl i j))
                      (setf i j)))
                   (t
                    (let ((end (position #\} tpl :start i)))
                      (if (null end)
                          (progn (lit (subseq tpl i)) (setf i n))
                          (let ((slot (subseq tpl (1+ i) end)))
                            (setf i (1+ end))
                            (cond
                              ((equal slot "*") (push (list :join 0) out))
                              ((and (plusp (length slot))
                                    (char= (char slot (1- (length slot))) #\:)
                                    (slot-index (subseq slot 0 (1- (length slot)))))
                               (push (list :join (slot-index (subseq slot 0 (1- (length slot))))) out))
                              ((slot-index slot) (push (list :splice (slot-index slot)) out))
                              (t (push (list :lexical slot) out)))))))))))
    (nreverse out)))

(defun fill-segments (dialect segments args &optional pos expanding)
  "Fill a template with already-rendered arguments, producing a part list.

Splicing part lists rather than strings is the whole point: an argument carrying
parameter slots keeps them. Concatenating the arguments into strings first would
work exactly until a literal contained something that looked like a placeholder.

Slot numbers are ABSOLUTE from the moment the literal is created -- one
translation has one parameter vector, held by the translator, and an
intermediate fragment carries indices into it rather than a vector of its own.
So splicing copies slots verbatim and never renumbers.

EXPANDING is the set of lexical keys this call is already inside. A lexical
value may reference another, and nothing stopped one from referencing itself: a
dialect registering (:textCast \"X({textCast:0})\") recursed until the host
died. The cycle is refused rather than a depth capped, because the cycle is the
actual mistake and a depth cap would need a number nobody can justify."
  (let ((parts '()))
    (labels ((push-str (s)
               (when (plusp (length s))
                 (if (and parts (stringp (car parts)))
                     (setf (car parts) (concatenate 'string (car parts) s))
                     (push s parts))))
             (splice (f)
               (dolist (p (fragment-parts f))
                 (if (stringp p) (push-str p) (push p parts))))   ; absolute already
             (join-from (k)
               (loop for i from k below (length args)
                     do (unless (= i k) (push-str ", "))
                        (splice (nth i args))))
             (lexical (slot)
               (let* ((colon (position #\: slot))
                      (key (if colon (subseq slot 0 colon) slot))
                      (arg (if colon (subseq slot (1+ colon)) ""))
                      (val (dialect-lexical dialect key)))
                 (unless (stringp val)
                   (refuse "E_SQL_UNSUPPORTED"
                           (format nil "a template used {~a}, ~
                                        which is neither an argument nor a lexical entry of dialect ~a"
                                   slot dialect)
                           pos))
                 (if (equal arg "")
                     (push-str val)
                     (progn
                       (when (member key expanding :test #'equal)
                         (refuse "E_SQL_UNSUPPORTED"
                                 (format nil "the ~a lexical entry ~
                                              of dialect ~a expands into itself, so filling it would never finish"
                                         key dialect)
                                 pos))
                       ;; {key:*} is {key:n} for every argument, joined with ", "
                       ;; (sql/MAP.md 4.2).
                       (let ((each (if (equal arg "*")
                                       (loop for n below (length args)
                                             collect (format nil "~d" n))
                                       (list arg))))
                         (loop for one in each
                               for at from 0
                               do (when (> at 0) (push-str ", "))
                                  ;; binaryCast converts a TEXT or NUM operand to
                                  ;; bytes. One already BIN needs no conversion,
                                  ;; and on PostgreSQL converting it is
                                  ;; destructive: text::bytea parses its input as a
                                  ;; bytea LITERAL. Every other cast is idempotent
                                  ;; and applied unconditionally; this is the one
                                  ;; whose input kind decides whether it means
                                  ;; anything.
                                  (let ((ca (and (equal key "binaryCast")
                                                 (slot-index one))))
                                    (if (and ca (< ca (length args))
                                             (eq (fragment-kind (nth ca args)) :bin))
                                        (splice (nth ca args))
                                        (dolist (p (fill-segments
                                                    dialect
                                                    (lexical-expansion val one)
                                                    args pos (cons key expanding)))
                                          (if (stringp p) (push-str p) (push p parts))))))))))))
      (dolist (seg segments)
        (if (stringp seg)
            (push-str seg)
            (ecase (car seg)
              (:join (join-from (cadr seg)))
              (:splice (let ((k (cadr seg)))
                         (when (>= k (length args))
                           (refuse "E_SQL_UNSUPPORTED"
                                   (format nil "the mapping for this ~
expression asks for argument ~a, which it was not given" k) pos))
                         (splice (nth k args))))
              (:lexical (lexical (cadr seg))))))
      (nreverse parts))))

(defvar *lexical-expansions* (make-hash-table :test #'eq :weakness :key :synchronized t)
  "For a lexical template VALUE, the segments of its expansion for each argument
spelling ONE (an alist): `{key:one}` is VALUE with `{0}` replaced by `{one}`, which
was rebuilt and re-scanned at every use.")

(defun lexical-expansion (val one)
  (let ((cell (assoc one (gethash val *lexical-expansions*) :test #'string=)))
    (if cell
        (cdr cell)
        (let ((segs (template-segments (replace-all val "{0}" (format nil "{~a}" one)))))
          (push (cons one segs) (gethash val *lexical-expansions*))
          segs))))

(defun emit-fill (dialect tpl args &optional pos expanding)
  "Fill the template TPL (see FILL-SEGMENTS): the segments of a mapping template are
parsed once per template string and kept."
  (fill-segments dialect
                 (if expanding
                     (template-segments tpl)
                     (or (gethash tpl *template-segments*)
                         (setf (gethash tpl *template-segments*) (template-segments tpl))))
                 args pos expanding))
