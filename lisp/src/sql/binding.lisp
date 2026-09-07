;;;; How an application says where a SEL variable lives in the schema, and what
;;;; an aggregate binder names for the duration of one element.
;;;;
;;;; Constructed in code, never decoded from a document. That is the whole
;;;; point: this layer used to take a nested map shaped like JSON and validate
;;;; it by hand, and a cross-host review found the hosts disagreeing about what
;;;; a malformed one meant. A typed constructor makes the whole class
;;;; unrepresentable rather than refusable.
;;;;
;;;; See docs/SQL-TRANSLATION.md §5.

(in-package #:sel.sql)

(defstruct (binding (:constructor %binding (kind spec)))
  "KIND is :column, :columns, :relation or :value; SPEC is the plist the
translator reads. Nothing outside this file builds one."
  (kind :column)
  (spec '() :type list))

;;; Every refusal here is a SQL-ERROR with E_SQL_BINDING, which is the class an
;;; application catches -- and which TRY-TRANSLATE therefore swallows, exactly
;;; as it does in the other hosts.

(defun check-string (what v)
  (unless (stringp v)
    (refuse "E_SQL_BINDING" (format nil "~a must be a string" what))))

(defun check-name (what v)
  "An identifier the application supplied has to survive being quoted.

EMIT-IDENT doubles the quote character and passes everything else through,
which is right for every character but two. A NUL terminates the C string libpq
and sqlite3 are handed, so `a\\0b` is malformed SQL on all four servers rather
than a column nobody has. An empty name quotes to \"\", which PostgreSQL rejects
and the other three accept -- a divergence with no upside."
  (check-string (format nil "a binding's ~a" what) v)
  (when (zerop (length v))
    (refuse "E_SQL_BINDING" (format nil "a binding has an empty ~a name" what)))
  (when (find (code-char 0) v)
    (refuse "E_SQL_BINDING"
            (format nil "a binding has a ~a name containing a NUL, which no ~
dialect can quote" what))))

(defun check-binding-type (type)
  (unless (member type +kinds+)
    (refuse "E_SQL_BINDING"
            (format nil "a binding has type ~s; use one of ~{~a~^, ~}"
                    type (mapcar #'kind-name +kinds+)))))

(defun check-numeric (where v)
  "Every scalar reachable from a NUM-typed value binding."
  (when (plusp (sel:value-size v))
    (dolist (cell (sel:value-entries v))
      (check-numeric (format nil "~a[~s]" where (car cell)) (cdr cell)))
    (return-from check-numeric))
  (when (sel:value-none-p v) (return-from check-numeric))
  (unless (and (sel:value-text-p v) (sel:looks-numeric v))
    (refuse "E_SQL_BINDING"
            (format nil "~a declares type NUM, which asks for it to be emitted ~
unquoted, but that is not a number" where)))
  ;; LOOKS-NUMERIC is broader than canonical, and the emitter writes the
  ;; canonical form rather than the caller's characters -- right for the AST
  ;; path, where the lexer has already canonicalised, and wrong here, where the
  ;; application supplied the string and the evaluator was handed that same
  ;; string. "007" translated to 7 while SEL kept "007".
  ;;
  ;; Unguarded, and it cannot signal: LOOKS-NUMERIC has already answered NIL for
  ;; anything past spec/SPEC.md §6.4's cap, so the parse below has already
  ;; succeeded once on this text.
  (let ((text (sel:as-text v)))
    (unless (equal (sel:as-text (sel:make-num text)) text)
      (refuse "E_SQL_BINDING"
              (format nil "~a declares type NUM and is ~s, which is not how SEL ~
writes that number; a NUM binding is emitted unquoted and must already be ~
canonical, so pass it as text or drop the leading zeros" where text)))))

;;; --- the four kinds -------------------------------------------------------

(defun binding-column (column &optional table (type :unknown))
  "One column, optionally qualified by a table, optionally typed.

TYPE is what the kind guards read. Leaving it :UNKNOWN is honest and costs the
guards: an UNKNOWN operand passes every check, because the binding did not say
and nothing here can either."
  (check-name "column" column)
  (when table (check-name "table" table))
  (check-binding-type type)
  (%binding :column (list :column column :table table :type type)))

(defun binding-raw (sql &optional (type :unknown))
  "A column expressed as SQL this layer will not read.

The one place an application writes SQL here. It is emitted verbatim, so
whatever it contains is the application's promise rather than this layer's --
which is exactly why it is a named constructor and not a key somebody can leave
in a map by accident."
  (check-string "a raw column binding" sql)
  (when (zerop (length sql))
    (refuse "E_SQL_BINDING" "a raw column binding cannot be empty"))
  (check-binding-type type)
  (%binding :column (list :raw sql :type type)))

(defun binding-columns (&rest items)
  "An ordered set of columns, iterated by an aggregate and indexed by position:
the first is V[1]."
  (unless items
    (refuse "E_SQL_BINDING" "a columns binding needs at least one column"))
  (let ((out '()))
    (loop for item in items
          for i from 1
          do (unless (and (binding-p item) (eq (binding-kind item) :column))
               (refuse "E_SQL_BINDING"
                       (format nil "a columns binding takes column bindings, and ~
item ~a is not one" i)))
             (push (binding-spec item) out))
    (%binding :columns (list :items (nreverse out)))))

(defun %make-relation (from from-raw-p alias fields scalar correlate)
  (let ((out '()))
    (dolist (cell fields)
      (let ((name (car cell)) (b (cdr cell)))
        (unless (and (binding-p b) (eq (binding-kind b) :column))
          (refuse "E_SQL_BINDING"
                  (format nil "the field ~a of a relation binding must be a ~
column binding" name)))
        ;; ASCII-UPCASE once, here, so every consumer looks a field up the same
        ;; way. A Unicode upper-caser would fold "ß" to "SS" and change the
        ;; key's length.
        (push (cons (sel::ascii-upcase name) (binding-spec b)) out)))
    (setf out (nreverse out))
    (when scalar (check-string "a relation binding's scalar" scalar))
    (when correlate (check-string "a relation binding's correlate" correlate))
    (when (and scalar (not (assoc (sel::ascii-upcase scalar) out :test #'equal)))
      (refuse "E_SQL_BINDING"
              (format nil "a relation binding names ~a as its scalar, which is ~
not one of its fields" scalar)))
    (%binding :relation (list :from from :from-raw-p from-raw-p :alias alias
                              :fields out :scalar scalar :correlate correlate))))

(defun binding-relation (from &optional alias fields scalar correlate)
  "A set of rows, rendered as a correlated subquery.

FIELDS maps a SEL key to a column binding, as an ALIST -- the order is the
order they were given. SCALAR names the field a bare reference means, and only
a ONE-field relation may declare it: a wider row is a map in SEL, and a map is
not the value of one of its fields. CORRELATE is SQL, like RAW, and joins the
subquery back to the outer row."
  (check-name "from" from)
  (when alias (check-name "alias" alias))
  (%make-relation from nil alias fields scalar correlate))

(defun binding-relation-query (query &optional alias fields scalar correlate)
  "The same, over a query the application writes rather than a table."
  (check-string "a relation query" query)
  (when (zerop (length query))
    (refuse "E_SQL_BINDING" "a relation query cannot be empty"))
  (when alias (check-name "alias" alias))
  (%make-relation query t alias fields scalar correlate))

(defun binding-value (v &optional type)
  "A constant the application supplies, inlined as a literal.

Takes a SEL value, never a native number or string, and that is the fix for the
last cross-host divergence here: PHP's json_decode turns a 20-digit integer into
a float, Python keeps it exact, and JS cannot tell 1.0 from 1. Asking the caller
for a value moves the decision to the line that knows the answer.

TYPE is :NUM or nothing. It decides whether the value is emitted quoted, which
is a question no inspection can settle: SEL numbers ARE text values, so
(make-num \"5.00\") and (make-text \"5.00\") are one object."
  (unless (sel:value-p v)
    (refuse "E_SQL_BINDING" "a value binding takes a SEL value"))
  (when type (check-binding-type type))
  (when (eq type :num) (check-numeric "this value binding" v))
  (%binding :value (list :value v :type type)))

;;; --- the set an application hands to TRANSLATE ----------------------------

(defstruct (binding-map (:constructor %binding-map (sorted order)))
  "SORTED is what NAMES and the \"bound names are ...\" message want; ORDER is
the caller's own, which is what CHECK-ALIASES wants -- the dynamic hosts iterate
an insertion-ordered dict there, and iterating the sorted one would name a
different one of two colliding relations."
  (sorted '() :type list)
  (order '() :type list))

(defun make-bindings (alist)
  (let ((sorted '()) (order '()))
    (dolist (cell alist)
      (let* ((key (sel::ascii-upcase (car cell)))
             (existing (assoc key sorted :test #'equal)))
        (unless (binding-p (cdr cell))
          (refuse "E_SQL_BINDING"
                  (format nil "the binding for ~a is not a binding; build one ~
with BINDING-COLUMN, -COLUMNS, -RELATION, -RELATION-QUERY, -RAW or -VALUE"
                          (car cell))))
        ;; A duplicate name keeps its FIRST position and takes the LAST value,
        ;; which is what assigning into a dict twice does.
        (if existing
            (setf (cdr existing) (cdr cell))
            (progn (push (cons key (cdr cell)) sorted) (push key order)))))
    (%binding-map (sort (nreverse sorted) #'string< :key #'car) (nreverse order))))

(defun bindings-has (bs name)
  (and (assoc (sel::ascii-upcase name) (binding-map-sorted bs) :test #'equal) t))

(defun bindings-names (bs) (mapcar #'car (binding-map-sorted bs)))

(defun bindings-get (bs name &optional pos)
  (let* ((key (sel::ascii-upcase name))
         (cell (assoc key (binding-map-sorted bs) :test #'equal)))
    (unless cell
      (refuse "E_SQL_UNBOUND"
              (format nil "~a is read by this rule but no binding says where it ~
lives~a" key
                      (if (binding-map-sorted bs)
                          (format nil "; bound names are ~{~a~^, ~}" (bindings-names bs))
                          "; no bindings were given"))
              pos))
    (cdr cell)))

(defun relation-alias (rel)
  "The alias a relation renders under -- its own, or the table name when it
declares none. The same rule CHECK-ALIASES applies."
  (let ((alias (getf rel :alias)))
    (if (and (stringp alias) (plusp (length alias))) alias (getf rel :from))))

(defun bindings-check-aliases (bs &optional pos)
  "A relation alias may name only one thing. Two relations sharing an alias in
one expression would produce a subquery correlated to the wrong rows, and the
host chose the aliases, so the host can fix them."
  (let ((seen '()))
    (dolist (name (binding-map-order bs))
      (let ((b (cdr (assoc name (binding-map-sorted bs) :test #'equal))))
        (when (eq (binding-kind b) :relation)
          (let* ((alias (relation-alias (binding-spec b)))
                 (prev (assoc alias seen :test #'equal)))
            (when prev
              (refuse "E_SQL_BINDING"
                      (format nil "relations ~a and ~a share the alias ~a; give ~
each one its own" (cdr prev) name alias)
                      pos))
            (push (cons alias name) seen)))))))

;;; --- binders --------------------------------------------------------------

(defstruct (binder (:constructor %binder (shape payload &optional reason)))
  "What an aggregate binder names for the duration of one element. Three shapes
matching the three iteration shapes of docs/SQL-TRANSLATION.md §7, plus one that
exists only to carry a refusal -- so that `_K` inside a relation body fails
saying rows have no key, rather than falling through to the bindings map and
being reported as an unbound variable."
  (shape :none)          ; :node :column :row :none
  (payload nil)
  (reason nil))

(defun binder-node (node) (%binder :node node))
(defun binder-column (spec) (%binder :column spec))
(defun binder-row (rel) (%binder :row rel))
(defun binder-none (reason) (%binder :none nil reason))
