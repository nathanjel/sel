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

(defun emit-text-literal (dialect text)
  (let ((quote (lex-text dialect "textQuote"))
        (escape (dialect-lexical dialect "textEscape")))
    (if (not (and escape (listp escape)))
        (concatenate 'string quote text quote)
        ;; Longest first, so a rule for "\\\\" is applied before one for "\\".
        ;; A single left-to-right pass, never one replace per rule: replacing '
        ;; with '' and then \ with \\ would rewrite the output of the first.
        (let ((rules (stable-sort (copy-list escape) #'> :key (lambda (c) (length (car c))))))
          (with-output-to-string (out)
            (write-string quote out)
            (let ((i 0) (n (length text)))
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
                             (progn (write-char (char text i) out) (incf i))))))
            (write-string quote out))))))

(defun bytes-to-hex (bytes)
  (string-downcase (with-output-to-string (o)
                     (loop for b across bytes do (format o "~2,'0X" b)))))

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
       (replace-all tpl "{hex}" (bytes-to-hex (sel:as-bytes v pos)))))
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
  (if (eq (fragment-kind f) :num)
      f
      ;; DIALECT-LEXICAL rather than LEX-TEXT, whose message names the missing
      ;; key. The key is not what an author can act on here; declaring the
      ;; binding NUM is, and that is the sentence this refusal has to say.
      (progn
        (check-numeric-guard dialect)
        (let ((guard (dialect-lexical dialect "numericGuard")))
          (unless (stringp guard)
            (refuse "E_SQL_UNSUPPORTED"
                    (format nil "dialect ~a has no way to ask whether a value is a ~
number, so an operand it has not been told is one cannot be read as one here; ~
declare the binding NUM if the column really is numeric" dialect)
                    pos))
          (%fragment (emit-fill dialect guard (list f) pos) :num dialect)))))

(defun emit-text-operand (dialect f)
  "An operand of a byte comparison: cast to a character type, then given the
dialect's binary collation.

Both halves are needed and neither is enough alone. Without the collation
MariaDB's default is case-insensitive, so `\"A\" $== \"a\"` is true there and
false in SEL. Without the cast the collation does not stop two numeric operands
being compared as numbers, so `3.0 EQL 3` is true there and false in SEL -- EQL
is structural and does not normalise numbers."
  (let ((cast (dialect-lexical dialect "textCast"))
        (collate (dialect-lexical dialect "textCollate"))
        (parts (fragment-parts f)))
    (when (and (stringp cast) (not (equal cast "{0}")))
      (setf parts (emit-fill dialect cast (list f))))
    (when (and (stringp collate) (plusp (length collate)))
      (setf parts (append parts (list collate))))
    (%fragment parts :text dialect)))

(defun emit-fill (dialect tpl args &optional pos expanding)
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
                        (splice (nth i args)))))
      (let ((i 0) (n (length tpl)))
        (loop while (< i n)
              do (let ((c (char tpl i)))
                   (cond
                     ((and (char= c #\{) (< (1+ i) n) (char= (char tpl (1+ i)) #\{))
                      (push-str "{") (incf i 2))
                     ((and (char= c #\}) (< (1+ i) n) (char= (char tpl (1+ i)) #\}))
                      (push-str "}") (incf i 2))
                     ((char/= c #\{) (push-str (string c)) (incf i))
                     (t
                      (let ((end (position #\} tpl :start i)))
                        (if (null end)
                            (progn (push-str (subseq tpl i)) (setf i n))
                            (let ((slot (subseq tpl (1+ i) end)))
                              (setf i (1+ end))
                              (cond
                                ((equal slot "*") (join-from 0))
                                ((and (plusp (length slot))
                                      (char= (char slot (1- (length slot))) #\:)
                                      (slot-index (subseq slot 0 (1- (length slot)))))
                                 (join-from (slot-index (subseq slot 0 (1- (length slot))))))
                                ((slot-index slot)
                                 (let ((k (slot-index slot)))
                                   (when (>= k (length args))
                                     (refuse "E_SQL_UNSUPPORTED"
                                             (format nil "the mapping for this ~
expression asks for argument ~a, which it was not given" k) pos))
                                   (splice (nth k args))))
                                (t
                                 ;; A lexical reference, from a runtime-registered
                                 ;; template.
                                 (let* ((colon (position #\: slot))
                                        (key (if colon (subseq slot 0 colon) slot))
                                        (arg (if colon (subseq slot (1+ colon)) ""))
                                        (val (dialect-lexical dialect key)))
                                   (unless (stringp val)
                                     (refuse "E_SQL_UNSUPPORTED"
                                             (format nil "a template used {~a}, ~
which is neither an argument nor a lexical entry of dialect ~a" slot dialect) pos))
                                   (if (equal arg "")
                                       (push-str val)
                                       (progn
                                         (when (member key expanding :test #'equal)
                                           (refuse "E_SQL_UNSUPPORTED"
                                                   (format nil "the ~a lexical entry ~
of dialect ~a expands into itself, so filling it would never finish" key dialect)
                                                   pos))
                                         ;; binaryCast converts a TEXT or NUM
                                         ;; operand to bytes. One already BIN needs
                                         ;; no conversion, and on PostgreSQL
                                         ;; converting it is destructive:
                                         ;; text::bytea parses its input as a bytea
                                         ;; LITERAL. Every other cast is idempotent
                                         ;; and applied unconditionally; this is the
                                         ;; one whose input kind decides whether it
                                         ;; means anything.
                                         (let ((ca (and (equal key "binaryCast")
                                                        (slot-index arg))))
                                           (if (and ca (< ca (length args))
                                                    (eq (fragment-kind (nth ca args)) :bin))
                                               (splice (nth ca args))
                                               (dolist (p (emit-fill
                                                           dialect
                                                           (replace-all val "{0}"
                                                                        (format nil "{~a}" arg))
                                                           args pos (cons key expanding)))
                                                 (if (stringp p) (push-str p) (push p parts)))))))))))))))))
      (nreverse parts)))))
