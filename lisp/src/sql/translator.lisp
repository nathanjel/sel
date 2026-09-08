;;;; Stage 2: the walk that turns one normalised expression into one fragment.
;;;;
;;;; Kind inference is folded into this same walk rather than run as a separate
;;;; pass, because post-order means every operand's kind is already known when
;;;; its parent needs it. Nothing becomes characters until AS-VALUE or
;;;; AS-CONDITION is called, so a kind failure escapes with no partial output.

(in-package #:sel.sql)

(defstruct (translator (:constructor %translator (dialect bindings strict)))
  (dialect "" :type string)
  (bindings nil)
  (strict nil)
  ;; The single absolute parameter vector, held REVERSED while the walk runs so
  ;; a push is O(1); the slot id is its length after the push. Slot ids are
  ;; CREATION numbers and are never renumbered: the template decides where a
  ;; slot lands, so the second slot created can be the first emitted, and
  ;; params[slot - 1] is the value forever whatever the template does.
  (params '() :type list)
  ;; Parallel to PARAMS, and never :UNKNOWN or :LIST -- those are clamped to
  ;; :TEXT, because they are not literal FORMS and the renderer has to have one.
  (param-kinds '() :type list)
  ;; An insertion-ordered set, held reversed. A sorted container would reorder
  ;; what the fragment reports, and the .sqlt cases can see that.
  (caveats '() :type list)
  (frames '() :type list)
  (const-names '() :type list)
  (const-root nil)
  (depth 0 :type fixnum))

(defun list-key (k)
  "The 1-based list position a key names, or NIL when it names none.

`[1-9][0-9]{0,8}`, matched WHOLE. SEL list keys are the canonical decimals, so
\"01\" is not a key and neither is \"1\\n\", and the evaluator answers E_NO_KEY
for both. This layer used to answer *element 1* for both, in every host, because
`^[0-9]+$` accepts a trailing newline and the integer parsers accept leading
zeros.

Nine digits at most, so the conversion is exact in every host that will ever
implement this. No list this layer can build has a billion elements, so the cap
costs nothing and removes the question."
  (when (and (stringp k) (<= 1 (length k) 9)
             (char<= #\1 (char k 0) #\9)
             (every #'ascii-digit-p k))
    (parse-integer k)))

(defun declared-kind (spec v)
  "The literal form of a value the HOST supplied, where no AST node exists to
say whether the author wrote 5.00 or \"5.00\".

Never guesses from LOOKS-NUMERIC: doing so would emit a product code \"00123\"
as the number 123, and a `$==` rule over it would then be answered by the
database instead of by SEL."
  (cond ((sel:value-bool-p v) :bool)
        ((sel:value-bin-p v) :bin)
        ((sel:value-none-p v) :list)
        ((eq (getf spec :type) :num) :num)
        (t :text)))

(defun child-of (n key)
  "Resolve a key against a static-list element. Two different key semantics in
one function: numeric position for a `list`, EXACT string for a `clist`."
  (case (snode-kind n)
    (:list (let ((i (list-key key)))
             (when (and i (<= i (length (sel::node-items n))))
               (nth (1- i) (sel::node-items n)))))
    ;; Insertion-ordered, first match. Stage 1 already refused duplicates.
    (:clist (cdr (assoc key (clist-entries n) :test #'equal)))
    (t nil)))

(defun add-caveat (tr name)
  (pushnew name (translator-caveats tr) :test #'equal))

(defun make-literal (tr v kind)
  "The ONLY writer of PARAMS and PARAM-KINDS."
  (push v (translator-params tr))
  (push (if (member kind '(:unknown :list)) :text kind) (translator-param-kinds tr))
  ;; The fragment's own kind stays UNCLAMPED -- a LIST-kinded literal fragment
  ;; exists and is refused later by AS-VALUE.
  (%fragment (list (length (translator-params tr))) kind (translator-dialect tr)))

(defun find-binder (tr name)
  "Innermost frame wins, which is the precedence SEL's own context lookup gives
a binder over a variable."
  (dolist (frame (translator-frames tr) nil)
    (let ((cell (assoc name frame :test #'equal)))
      (when cell (return (cdr cell))))))

;;; --- the walk -------------------------------------------------------------

(defun walk-node (tr n)
  ;; Bounded at the evaluator's own limit. Nothing bounded it, so a flat chain
  ;; of 201 terms over a column translated -- and the evaluator answers E_DEPTH
  ;; for that same expression.
  (incf (translator-depth tr))
  (when (> (translator-depth tr) sel::+max-depth+)
    (decf (translator-depth tr))
    (refuse "E_SQL_DEPTH"
            (format nil "this expression nests deeper than SEL will evaluate ~
(~a), so there is nothing to translate; the evaluator answers E_DEPTH for it"
                    sel::+max-depth+)
            (snode-pos n)))
  (unwind-protect
       (let ((compound (member (snode-kind n) '(:bin :un :call))))
         (if (not (and compound (is-constant n (translator-const-names tr))))
             (dispatch tr n)
             ;; Ask SEL whether the expression is VALID before asking the map
             ;; whether it is translatable -- and at EVERY compound node, not
             ;; just the outermost. Checking only the outermost looks like a free
             ;; optimisation and is not, because SEL is lazy: FALSE AND (1/0 > 0)
             ;; is constant and SEL answers FALSE without ever dividing, so
             ;; validating the AND alone accepts it and (1/0) goes into the SQL.
             ;;
             ;; AFTER the dispatch, so every refusal the translator already had
             ;; keeps its own message.
             (let ((f (dispatch tr n)))
               (validate-constant n (translator-const-root tr))
               f)))
    (decf (translator-depth tr))))

(defun dispatch (tr n)
  (case (snode-kind n)
    (:num (make-literal tr (sel:make-num (sel::node-s n)) :num))
    (:text (make-literal tr (sel:make-text (sel::node-s n)) :text))
    (:bool (make-literal tr (sel:make-bool (sel::node-b n)) :bool))
    (:var (translate-variable tr n))
    (:index (translate-index tr n))
    (:un (translate-unary tr n))
    (:bin (translate-binary tr n))
    ((:list :clist)
     (refuse "E_SQL_SHAPE"
             "a list is not a SQL value; a list can only be the thing an ~
aggregate iterates" (snode-pos n)))
    (:call (translate-call tr n))
    (t (refuse "E_SQL_SHAPE"
               (format nil "cannot translate a ~(~a~) node" (snode-kind n))
               (snode-pos n)))))

;;; --- values ---------------------------------------------------------------

(defun column-ref (tr spec)
  "Turns a column SPEC into a fragment with no parameter slots. A raw binding is
emitted VERBATIM -- the one place application-written SQL enters, which is why
it is a named constructor and not a map key."
  (let ((sql (if (getf spec :raw)
                 (getf spec :raw)
                 (emit-column (translator-dialect tr) (getf spec :table)
                              (getf spec :column)))))
    (%fragment (list sql) (or (getf spec :type) :unknown) (translator-dialect tr))))

(defun translate-variable (tr n)
  (let ((name (sel::node-s n)))
    ;; A binder wins over the bindings map, without ever consulting it.
    (let ((bound (find-binder tr name)))
      (when bound (return-from translate-variable (from-binder tr bound n))))
    (let* ((b (bindings-get (translator-bindings tr) name (snode-pos n)))
           (spec (binding-spec b)))
      (case (binding-kind b)
        (:column (column-ref tr spec))
        (:value
         (let ((v (getf spec :value)))
           ;; Size before none, so a NONE value WITH children is a list and gets
           ;; the list message.
           (when (plusp (sel:value-size v))
             (refuse "E_SQL_SHAPE"
                     (format nil "~a is bound to a list, and a list is not a SQL ~
value; it can only be the thing an aggregate iterates" name)
                     (snode-pos n)))
           (when (sel:value-none-p v)
             (refuse "E_SQL_SHAPE"
                     (format nil "~a is bound to an empty value, which is not a ~
SQL value; only an aggregate can be given an empty binding" name)
                     (snode-pos n)))
           (make-literal tr v (declared-kind spec v))))
        ((:columns :relation)
         (refuse "E_SQL_SHAPE"
                 (format nil "~a is bound as a ~(~a~), which names a set of ~
values rather than one; use it as the first argument of an aggregate, not as a ~
value on its own" name (binding-kind b))
                 (snode-pos n)))
        (t (refuse "E_SQL_BINDING" (format nil "unusable binding for ~a" name)
                   (snode-pos n)))))))

(defun constant-index (idx)
  "The whole gate on what may appear between the brackets. `num` and `text`
only -- a bool, a variable, a call and an arithmetic expression are all refused,
`A[1+1]` included, because stage 1 does no constant folding."
  (if (member (snode-kind idx) '(:num :text))
      (sel::node-s idx)
      (refuse "E_SQL_SHAPE"
              "an index must be a constant here: the column it names has to be ~
known before the query runs" (snode-pos idx))))

(defun translate-index (tr n)
  (let ((obj (sel::node-l n)))
    ;; A PARENTHESISED variable still has kind :var -- the parser sets only a
    ;; GROUPED flag -- so (C)[1] reaches the same path as C[1].
    (unless (eq (snode-kind obj) :var)
      (refuse "E_SQL_SHAPE"
              "only a bound name can be indexed here; SQL has no way to index ~
into the result of an expression" (snode-pos n)))
    (let ((name (sel::node-s obj))
          (bound (find-binder tr (sel::node-s obj))))
      ;; On the binder path the key is computed INSIDE the call, so
      ;; CONSTANT-INDEX runs before any binder-shape check.
      (when bound
        (return-from translate-index
          (index-binder tr bound name (constant-index (sel::node-r n)) n)))
      ;; Off it, BINDINGS-GET runs FIRST: for an unbound name with a non-constant
      ;; index, E_SQL_UNBOUND wins over E_SQL_SHAPE.
      (let* ((b (bindings-get (translator-bindings tr) name (snode-pos obj)))
             (spec (binding-spec b))
             (key (constant-index (sel::node-r n))))
        (case (binding-kind b)
          (:relation
           (refuse "E_SQL_SHAPE"
                   (format nil "~a is a relation, which is a list of rows; ~
indexing it names no value SEL can produce, so use an aggregate and index the ~
row its binder gives you" name)
                   (snode-pos n)))
          (:columns
           (let ((i (list-key key)) (items (getf spec :items)))
             (when (or (null i) (> i (length items)))
               (refuse "E_SQL_BINDING"
                       (format nil "~a[~a] is outside that binding's ~a column(s)"
                               name key (length items))
                       (snode-pos n)))
             (column-ref tr (nth (1- i) items))))
          (:value
           (let* ((v (getf spec :value))
                  (child (sel:value-get v key)))
             (unless child
               (refuse "E_SQL_BINDING"
                       (format nil "~a[~s] is not a key of that value" name key)
                       (snode-pos n)))
             (when (plusp (sel:value-size child))
               (refuse "E_SQL_SHAPE"
                       (format nil "~a[~s] is a list, not a SQL value" name key)
                       (snode-pos n)))
             (make-literal tr child (declared-kind spec child))))
          (t (refuse "E_SQL_SHAPE"
                     (format nil "~a is bound as a column, which has no parts to ~
index" name)
                     (snode-pos n))))))))

(defun from-binder (tr b n)
  "The binder half of a bare read."
  (case (binder-shape b)
    ;; Re-enters the whole walk on the element, so the depth counter and the
    ;; constant validation apply to the inlined element too.
    (:node (walk-node tr (binder-payload b)))
    (:column (column-ref tr (binder-payload b)))
    (:row
     (let* ((rel (binder-payload b))
            (fields (getf rel :fields))
            (name (sel::node-s n)))
       ;; The multi-field check comes FIRST, so a two-field relation that
       ;; declares a scalar still refuses as multi-field.
       (when (> (length fields) 1)
         (refuse "E_SQL_SHAPE"
                 (format nil "~a is a row of a relation with ~a fields, which is ~
a map in SEL and not one value; name the field you mean" name (length fields))
                 (snode-pos n)))
       (let* ((scalar (getf rel :scalar))
              (cell (and scalar (assoc (sel::ascii-upcase scalar) fields :test #'equal))))
         (unless cell
           (refuse "E_SQL_SHAPE"
                   (format nil "~a names a row, and the relation does not say ~
which of its fields a bare reference means; give the binding a \"scalar\", or ~
index the field you want" name)
                   (snode-pos n)))
         (column-ref tr (cdr cell)))))
    ;; How `_K` inside a relation body reports "a row of a relation has no key"
    ;; rather than being reported as an unbound variable.
    (:none (refuse "E_SQL_SHAPE" (binder-reason b) (snode-pos n)))
    (t (refuse "E_SQL_SHAPE" "unusable binder" (snode-pos n)))))

(defun index-binder (tr b name key n)
  (case (binder-shape b)
    (:row
     ;; The positional check comes before the field lookup, so a relation field
     ;; literally named "1" is unreachable through I[1] and I["1"] alike.
     (when (list-key key)
       (refuse "E_SQL_SHAPE"
               (format nil "~a[~a] asks for a row by position, and a relation has ~
no first row without an ORDER BY that nothing here can supply" name key)
               (snode-pos n)))
     (let* ((fields (getf (binder-payload b) :fields))
            (cell (assoc (sel::ascii-upcase key) fields :test #'equal)))
       (unless cell
         (refuse "E_SQL_BINDING"
                 (format nil "~a[~s] is not a field of that relation~a" name key
                         (if fields
                             (format nil "; it has ~{~a~^, ~}"
                                     (sort (mapcar #'car fields) #'string<))
                             "; it declares none"))
                 (snode-pos n)))
       (column-ref tr (cdr cell))))
    (:node
     (let ((elem (child-of (binder-payload b) key)))
       (unless elem
         (refuse "E_SQL_BINDING"
                 (format nil "~a[~s] is not a key of that element" name key)
                 (snode-pos n)))
       (walk-node tr elem)))
    ;; :COLUMN, and also :NONE -- which therefore does NOT report its reason when
    ;; indexed. A quirk, preserved.
    (t (refuse "E_SQL_SHAPE"
               (format nil "~a names a single column, which has no parts to index" name)
               (snode-pos n)))))

;;; --- kind guards ----------------------------------------------------------

(defparameter +numeric-ops+ '("==" "!=" "<" "<=" ">" ">="))
(defparameter +textual-ops+ '("$==" "$!=" "$<" "$<=" "$>" "$>=" "EQL"))
(defparameter +byte-comparisons+ '("$==" "$!=" "$<" "$<=" "$>" "$>=" "EQL" "IN"))
(defparameter +arithmetic-ops+ '("+" "-" "*" "/" "%"))

(defun eql-class (k)
  "The RUNTIME kind class EQL and IN compare, which is not the static kind. A SEL
number IS a text value (spec §4), so `1 EQL \"1\"` is TRUE and NUM and TEXT are
one class. BOOL and BIN are each their own.

:UNKNOWN and :LIST have NO class, and that absence is the mechanism: either one
passes."
  (case k ((:num :text) :text) (:bool :bool) (:bin :bin) (t nil)))

(defun require-comparable-kinds (l r op pos)
  (let ((cl (eql-class (fragment-kind l)))
        (cr (eql-class (fragment-kind r))))
    (when (and cl cr (not (eq cl cr)))
      ;; OTHER is computed as if L were always the BOOL side, so a TEXT-vs-BIN
      ;; mismatch says "compares a BOOL with a TEXT". A defect in the message,
      ;; kept: codes are contract and messages are not, and rewording it here
      ;; would make this the only host that did.
      (let ((other (if (eq (fragment-kind l) :bool) (fragment-kind r) (fragment-kind l))))
        (refuse "E_SQL_SHAPE"
                (format nil "~a compares a BOOL with a ~a, which SEL answers ~
FALSE for every value because the kinds differ. SQL has no way to say that: both ~
sides cast to the same characters" op (kind-name other))
                pos)))))

(defun require-bool (f pos where)
  ;; :UNKNOWN used to pass, on the reasoning that an undeclared column may well
  ;; be boolean and the database is the one that knows. Measured, the database
  ;; does not know: MariaDB answers `1 AND TRUE` as TRUE, so an undeclared
  ;; column holding 1 matched a row SEL refuses with E_NOT_BOOL, and PostgreSQL
  ;; raises 42804 instead. No dialect can ask "is this a boolean" -- in the
  ;; MySQL family a boolean IS a TINYINT, so testing IN (0, 1) would also admit
  ;; a NUM column SEL refuses -- so there is nothing to wrap it in, and refusing
  ;; is the only answer that keeps the warrant.
  (unless (eq (fragment-kind f) :bool)
    (refuse "E_SQL_SHAPE"
            (format nil "~a needs a BOOL here and this is ~a; SEL has no ~
truthiness, so neither does its translation" where (kind-name (fragment-kind f)))
            pos))
  f)

(defun require-num (f pos where)
  ;; Stricter than REQUIRE-NOT-BOOL: TEXT refuses here and passes there. SUM's
  ;; body must be statically numeric; an arithmetic operand need only not be
  ;; BOOL or BIN.
  (unless (member (fragment-kind f) '(:num :unknown))
    (refuse "E_SQL_SHAPE"
            (format nil "~a adds its body up, so it needs a number here and this ~
is ~a" where (kind-name (fragment-kind f)))
            pos))
  f)

(defun require-not-bool (f pos where)
  "Misnamed in every host, and kept: it refuses BOOL *and* BIN. NUM, TEXT,
UNKNOWN and LIST pass -- UNKNOWN because the binding did not say, so nothing
here can either."
  (when (member (fragment-kind f) '(:bool :bin))
    (refuse "E_SQL_SHAPE"
            (format nil "~a reads its operands as numbers, and ~a is not one; ~
SEL answers E_NOT_NUM here rather than coercing it" where
                    (if (eq (fragment-kind f) :bool) "a BOOL" "a BIN"))
            pos))
  (values))

(defun guard-numeric (tr f n)
  "Wrap an operand the numeric context cannot be sure of.

A constant is skipped, because REQUIRE-NUMERIC-CONSTANT has just proved it IS a
number -- guarding it would ask the server a question already answered here, and
would cost a bound value a second parameter for the repeated slot. What is left
is what could not be settled at translation time: columns, raw, relation fields."
  (if (is-constant n (translator-const-names tr))
      f
      (emit-numeric-operand (translator-dialect tr) f (snode-pos n))))

(defun require-numeric-constant (tr n)
  "An operand in a numeric position whose value is knowable here.

Takes a NODE, unlike the guards around it, which take the fragment that node
translated to. That is the whole point: the kind guards ask what the binding
DECLARED, this asks what the constant IS, and the second is a different and
stronger question wherever the answer is written down. See
VALIDATE-NUMERIC-CONSTANT for why refusing loses nothing, and for why it is
never keyed on a declared kind."
  (when (is-constant n (translator-const-names tr))
    (validate-numeric-constant n (translator-const-root tr)))
  (values))

(defun require-not-bool-operand (f pos where)
  "Distinct from REQUIRE-NOT-BOOL by exactly one kind: BIN passes here.
Deliberately NOT applied to EQL or IN, which are structural -- `TRUE EQL TRUE`
must stay TRUE."
  (when (eq (fragment-kind f) :bool)
    (refuse "E_SQL_SHAPE"
            (format nil "~a reads its operands as text or bytes, and a BOOL is ~
neither; SEL answers E_NOT_TEXT here rather than spelling it 1 or true" where)
            pos))
  (values))

(defun unify-kinds (fs pos)
  "The one kind a set of branches all produce.

UNKNOWN unifies with anything: that is what it is for. Two KNOWN kinds that
differ are another matter, and used to yield UNKNOWN as well. They cannot: SQL
types the whole CASE, and there is no rendering of the result that agrees with
SEL's. IF(TRUE, TRUE, \"A-1\") is the one the fuzz lane found."
  (let ((kind nil))
    (dolist (f fs (or kind :unknown))
      (let ((k (fragment-kind f)))
        (unless (eq k :unknown)
          (cond ((null kind) (setf kind k))
                ((not (eq kind k))
                 (refuse "E_SQL_SHAPE"
                         (format nil "these branches produce different kinds — ~
~a and ~a — and SQL gives the whole expression one type, which cannot match ~
SEL's for both" (kind-name kind) (kind-name k))
                         pos))))))))

(defun ret-kind (entry args pos)
  (let ((ret (getf entry :ret)))
    (cond
      ((equal ret "@concat")
       (if (some (lambda (a) (eq (fragment-kind a) :bin)) args) :bin :text))
      ((and (stringp ret) (unify-ret-p ret))
       (let ((pick '()) (rest (subseq ret 7)) (start 0))
         (loop for i from 0 to (length rest)
               do (when (or (= i (length rest)) (char= (char rest i) #\,))
                    (let ((k (parse-integer (subseq rest start i))))
                      (when (< k (length args)) (push (nth k args) pick)))
                    (setf start (1+ i))))
         (unify-kinds (nreverse pick) pos)))
      (t (or (kind-from-name ret) :unknown)))))

;;; --- the map application path ---------------------------------------------

(defun variant-for (op args)
  "Three selectors, fixed by sql/MAP.md §4.3 and identical in every host --
deliberately NOT data in the dialect map. The `num` test is AND and the `bin`
test is OR, and the asymmetry is intentional: an unknown numeric operand must be
coerced, an unknown concat operand must not be treated as bytes."
  (cond
    ((member op +numeric-ops+ :test #'equal)
     (if (and (eq (fragment-kind (first args)) :num)
              (eq (fragment-kind (second args)) :num))
         "num" "coerce"))
    ((member op +textual-ops+ :test #'equal) "text")
    ((equal op "&")
     (if (or (eq (fragment-kind (first args)) :bin)
             (eq (fragment-kind (second args)) :bin))
         "bin" "text"))
    (t nil)))

(defun template-of (tr entry args variant what pos)
  (let ((variants (plist-get entry :variants))
        (tpl (plist-get entry :tpl)))
    (when (presentp variants)
      (let ((arm (and variant (assoc variant variants :test #'equal))))
        (unless (and arm (cdr arm))
          (refuse "E_SQL_UNSUPPORTED"
                  (format nil "~a has no mapping in dialect ~a for ~a" what
                          (translator-dialect tr)
                          (if variant (format nil "~a operands" variant) "this shape"))
                  pos))
        (return-from template-of (cdr arm))))
    (when (stringp tpl) (return-from template-of tpl))
    ;; Arity-keyed. Membership FIRST, then the null test: a named count whose
    ;; template is NIL WITHDRAWS that arity, and the `*` fallback must not
    ;; rescue it. {"1": null, "*": "LEAST({*})"} refuses MIN(5) and still
    ;; answers MIN(5, 3).
    (let* ((n (format nil "~D" (length args)))     ; ~D, never PRINC-TO-STRING
           (arm (or (assoc n tpl :test #'equal) (assoc "*" tpl :test #'equal))))
      (unless (and arm (cdr arm))
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "~a has no mapping in dialect ~a for ~a argument(s); ~
it maps ~{~a~^, ~}" what (translator-dialect tr) n
                        (sort (mapcar #'car tpl) #'string<))
                pos))
      (cdr arm))))

(defun apply-entry (tr section key args pos &optional variant)
  (multiple-value-bind (entry found) (dialect-entry (translator-dialect tr) section key)
    (let ((what (if (eq section :ops) (format nil "the ~a operator" key) key))
          (d (translator-dialect tr)))
      (when (or (not found) (null entry))
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "~a has no mapping in dialect ~a" what d) pos))
      ;; The map's own reason, after an em dash. This is how BAND/BOR/BXOR are
      ;; refused: the entry is a string saying why no portable spelling exists.
      (when (stringp entry)
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "~a has no mapping in dialect ~a — ~a" what d entry) pos))
      (let ((builder (plist-get entry :builder)))
        (when (and (presentp builder) builder)
          ;; Skips arity, since, caveat and the template entirely.
          ;;
          ;; The DIALECT, not the translator: Python hands its builder
          ;; `self.emit` and C++ an `Emit&`, and this host's emit functions all
          ;; take a dialect as their first argument, so that IS the emitter
          ;; here. Passing the whole translator would make a builder written
          ;; against one host unusable on another.
          (return-from apply-entry (funcall builder d args pos))))
      (let ((arity (plist-get entry :arity)))
        (when (and (presentp arity) arity)
          (let ((n (length args)))
            (when (or (< n (car arity)) (> n (cdr arity)))
              (refuse "E_SQL_UNSUPPORTED"
                      (format nil "~a has no mapping in dialect ~a for ~a ~
argument(s)" what d n)
                      pos)))))
      (let ((since (plist-get entry :since)))
        (when (and (presentp since) since
                   (not (version-at-least (dialect-version d) since)))
          (refuse "E_SQL_DIALECT"
                  (format nil "~a needs ~a ~a or newer, and this map says ~a"
                          what d since (dialect-version d))
                  pos)))
      (let ((caveat (plist-get entry :caveat)))
        (when (and (presentp caveat) caveat)
          (when (translator-strict tr)
            (refuse "E_SQL_UNSUPPORTED"
                    (format nil "~a translates only approximately in dialect ~a ~
(~a), and strict mode refuses those" what d caveat)
                    pos))
          (add-caveat tr caveat)))
      (%fragment (emit-fill d (template-of tr entry args variant what pos) args pos)
                 (ret-kind entry args pos) d))))

(defun fold-pairwise (tr op parts pos)
  "Left-associative, through the operator's OWN template, so an unrolled
aggregate and a hand-written chain produce identical bytes. Never called with an
empty list: each caller supplies its aggregate's identity value instead, and
those differ per aggregate."
  (let ((acc (first parts)))
    (dolist (nxt (rest parts) acc)
      ;; Recomputed at EVERY step from the accumulator's CURRENT kind: for `&`
      ;; the accumulator becomes BIN as soon as any operand is, and stays BIN.
      (let ((pair (list acc nxt)))
        (setf acc (apply-entry tr :ops op pair pos (variant-for op pair)))))))

;;; --- operators ------------------------------------------------------------

(defun translate-unary (tr n)
  (let ((x (walk-node tr (sel::node-l n)))
        (op (sel::node-s n)))
    (if (equal op "NOT")
        (setf x (require-bool x (snode-pos (sel::node-l n)) "NOT"))
        (progn
          (require-not-bool x (snode-pos (sel::node-l n)) op)
          (require-numeric-constant tr (sel::node-l n))
          (setf x (guard-numeric tr x (sel::node-l n)))))
    ;; No variant: a unary entry must be a plain template.
    (apply-entry tr :ops op (list x) (snode-pos n))))

(defun translate-binary (tr n)
  (let ((op (sel::node-s n)))
    (when (equal op "IN") (return-from translate-binary (translate-in tr n)))
    ;; Strictly left then right: parameter slots are numbered in this order.
    (let ((l (walk-node tr (sel::node-l n)))
          (r (walk-node tr (sel::node-r n)))
          (lpos (snode-pos (sel::node-l n)))
          (rpos (snode-pos (sel::node-r n))))
      (when (member op '("AND" "OR" "XOR") :test #'equal)
        (setf l (require-bool l lpos op) r (require-bool r rpos op)))
      (when (or (member op +arithmetic-ops+ :test #'equal)
                (member op +numeric-ops+ :test #'equal))
        (require-not-bool l lpos op)
        (require-not-bool r rpos op)
        ;; And an operand whose value is written down has to BE a number. After
        ;; the BOOL guard, not before: `TRUE + 1` is E_SQL_SHAPE and stays that
        ;; way.
        (require-numeric-constant tr (sel::node-l n))
        (require-numeric-constant tr (sel::node-r n))
        ;; And an operand nobody has vouched for is wrapped so that a value SEL
        ;; would refuse becomes NULL rather than a number the server invented. A
        ;; NUM operand passes through untouched.
        (setf l (guard-numeric tr l (sel::node-l n))
              r (guard-numeric tr r (sel::node-r n))))
      ;; BAND/BOR/BXOR get NO kind guard: they are refused by the map entry.
      (when (or (equal op "&") (and (> (length op) 1) (char= (char op 0) #\$)))
        (require-not-bool-operand l lpos op)
        (require-not-bool-operand r rpos op))
      ;; Captured BEFORE the rewrite below, which forces both kinds to TEXT.
      (let ((variant (variant-for op (list l r))))
        (when (member op +byte-comparisons+ :test #'equal)
          (require-comparable-kinds l r op (snode-pos n))
          ;; The cast and collate are skipped only when BOTH operands are BIN.
          (unless (and (eq (fragment-kind l) :bin) (eq (fragment-kind r) :bin))
            (setf l (emit-text-operand (translator-dialect tr) l)
                  r (emit-text-operand (translator-dialect tr) r))))
        (apply-entry tr :ops op (list l r) (snode-pos n) variant)))))

;;; --- skeletons ------------------------------------------------------------
;;;
;;; A skeleton's placeholders are NAMED, not numbered, and the grammar is
;;; deliberately NOT EMIT-FILL's: no {{ }} escapes, no {*} or {n:}, no lexical
;;; expansion, no numeric slot grammar. Each difference is a behaviour a shared
;;; implementation would change.

(defun skeleton (tr name pos)
  (multiple-value-bind (s found) (dialect-entry (translator-dialect tr) :skel name)
    (let ((d (translator-dialect tr)))
      ;; Absent first, and separately from the string branch: reading the
      ;; sentinel as a refusal-with-reason would emit its text as the reason.
      (when (or (not found) (null s))
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "dialect ~a has no ~a skeleton" d name) pos))
      (when (stringp s)
        ;; The shipped map uses this for `join` in ansi and mysql-family.
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "dialect ~a cannot express ~a — ~a" d name s) pos))
      (let ((builder (plist-get s :builder)))
        (when (and (presentp builder) builder)
          ;; A hole the Python host has: map validation returns early on a
          ;; builder BEFORE the skel branch, so a builder registered against
          ;; `skel` registers and then hits a bare KeyError on s['tpl'] -- a host
          ;; crash escaping try_translate rather than a refusal.
          (refuse "E_SQL_UNSUPPORTED"
                  (format nil "the ~a skeleton for ~a is a builder, and a ~
skeleton is a template" name d)
                  pos)))
      (let ((caveat (plist-get s :caveat)))
        (when (and (presentp caveat) caveat)
          (when (translator-strict tr)
            (refuse "E_SQL_UNSUPPORTED"
                    (format nil "the ~a skeleton for ~a is not exactly equivalent ~
(~a), and strict mode refuses those" name d caveat)
                    pos))
          (add-caveat tr caveat)))
      (getf s :tpl))))

(defun fill-named (tr tpl slots pos)
  "Fills a skeleton. SLOTS is an alist of name to a LIST of items, each item a
string spliced as SQL text or a fragment whose parts are spliced -- preserving
parameter slots, which are absolute for the whole translation and never
renumbered."
  (let ((parts '()))
    (labels ((push-str (s)
               (when (plusp (length s))
                 (if (and parts (stringp (car parts)))
                     (setf (car parts) (concatenate 'string (car parts) s))
                     (push s parts)))))
      (let ((i 0) (n (length tpl)))
        (loop while (< i n)
              do (if (char/= (char tpl i) #\{)
                     (progn (push-str (string (char tpl i))) (incf i))
                     (let ((end (position #\} tpl :start i)))
                       ;; An unterminated brace emits the rest of the template
                       ;; literally. No refusal, in any host.
                       (if (null end)
                           (progn (push-str (subseq tpl i)) (setf i n))
                           (let* ((name (subseq tpl (1+ i) end))
                                  (cell (assoc name slots :test #'equal)))
                             (setf i (1+ end))
                             ;; Membership on the slot map the CALLER passed, not
                             ;; on the vocabulary.
                             (unless cell
                               (refuse "E_SQL_UNSUPPORTED"
                                       (format nil "a skeleton in dialect ~a uses ~
{~a}, which is not one of its slots" (translator-dialect tr) name)
                                       pos))
                             (dolist (item (cdr cell))
                               (if (stringp item)
                                   (push-str item)
                                   (dolist (p (fragment-parts item))
                                     (if (stringp p) (push-str p) (push p parts)))))))))))
      (nreverse parts))))

(defun merge-slots (a b)
  "Refuses to let one source shadow another.

A plain ERROR, not a SQL-ERROR: a collision is a bug in the translator, not a
refusable property of a rule or a map, so it must escape TRY-TRANSLATE the way
every other host mistake does."
  (dolist (cell b (append a b))
    (when (assoc (car cell) a :test #'equal)
      (error "two sources both supply the skeleton slot {~a}; one would silently ~
shadow the other" (car cell)))))

(defun relation-slots (tr rel)
  "{from} is the table and alias, or a query the binding carries; {corr} is the
join back to the outer row, or the dialect's TRUE when the binding has none -- an
uncorrelated relation is a subquery over the whole table, which is legal and
occasionally what you want."
  (let* ((d (translator-dialect tr))
         (from (if (getf rel :from-raw-p) (getf rel :from) (emit-ident d (getf rel :from))))
         (alias (getf rel :alias))
         (corr (getf rel :correlate)))
    (when (and alias (plusp (length alias)))
      (setf from (concatenate 'string from " " (emit-ident d alias))))
    (list (cons "from" (list from))
          (cons "corr" (list (or corr (lex-text d "true")))))))

;;; --- conditionals ---------------------------------------------------------

(defun lit-node (kind s b pos)
  "A synthetic literal node. GROUPED stays NIL: IS-BINDER-NAME rejects a grouped
var, and a synthetic node accidentally marked grouped would be refused as one."
  (let ((n (sel::make-node kind pos)))
    (setf (sel::node-s n) s (sel::node-b n) b)
    n))

(defun translate-conditional (tr n)
  (let* ((name (sel::node-s n))
         (args (copy-list (sel::node-items n))))
    ;; Spec §7.2's default: a TEXT literal of the empty string carrying the
    ;; CALL's position. Because the default is TEXT and not UNKNOWN, IF(p, 1)
    ;; refuses (NUM vs TEXT) and IF(p, TRUE) refuses (BOOL vs TEXT).
    (when (and (equal name "IF") (= (length args) 2))
      (setf args (append args (list (lit-node :text "" nil (snode-pos n))))))
    ;; BOTH fetched before any argument is walked, so a dialect that cannot
    ;; express CASE refuses before PARAMS grows and before any sub-refusal from
    ;; the branches can fire.
    (let* ((branch-tpl (skeleton tr "caseBranch" (snode-pos n)))
           (case-tpl (skeleton tr "case" (snode-pos n)))
           (last-i (1- (length args)))
           (results '())
           (joined '()))
      ;; Condition first, then result -- the walk order is part of the output,
      ;; because slot numbers are assigned in creation order.
      (loop for i from 0 below last-i by 2
            do (let* ((cond-f (require-bool (walk-node tr (nth i args))
                                            (snode-pos (nth i args)) name))
                      (then-f (walk-node tr (nth (1+ i) args))))
                 (push then-f results)
                 (let ((branch (%fragment
                                (fill-named tr branch-tpl
                                            (list (cons "cond" (list cond-f))
                                                  (cons "then" (list then-f)))
                                            (snode-pos n))
                                :unknown (translator-dialect tr))))
                   (when joined (push " " joined))
                   (push branch joined))))
      (let ((else-f (walk-node tr (nth last-i args))))
        (push else-f results)
        (%fragment (fill-named tr case-tpl
                               (list (cons "branches" (nreverse joined))
                                     (cons "else" (list else-f)))
                               (snode-pos n))
                   ;; Only the thens and the else; the conditions are BOOL and
                   ;; would poison it.
                   (unify-kinds (nreverse results) (snode-pos n))
                   (translator-dialect tr))))))

(defun case-when (tr cond-f then-f else-f pos)
  "The programmatic CASE builder, for SUM over an absorbed FILTER. Its kind rule
is deliberately NOT UNIFY-KINDS': two differing known kinds yield UNKNOWN here
where unify refuses, and an UNKNOWN `then` with a NUM `else` yields UNKNOWN
rather than NUM. Reusing unify would change the kind the enclosing SUM reports,
and so which downstream guards fire."
  (let ((branch (%fragment (fill-named tr (skeleton tr "caseBranch" pos)
                                       (list (cons "cond" (list cond-f))
                                             (cons "then" (list then-f)))
                                       pos)
                           :unknown (translator-dialect tr))))
    (%fragment (fill-named tr (skeleton tr "case" pos)
                           (list (cons "branches" (list branch))
                                 (cons "else" (list else-f)))
                           pos)
               (if (eq (fragment-kind then-f) (fragment-kind else-f))
                   (fragment-kind then-f)
                   :unknown)
               (translator-dialect tr))))

;;; --- calls ----------------------------------------------------------------

;;; Measured, not written: every non-lazy registry name was called with
;;; TO_UTF8("a") and with TRUE, and these are the ones SEL did not answer
;;; E_NOT_* for. Writing them by hand would be the second copy of SEL's argument
;;; rules that §11.4 exists to avoid.
(defparameter +bin-argument-ok+
  '("BLEN" "CRC32" "ENCODE_BASE64" "FROM_UTF8" "ISNUM" "TO_HEX" "TO_UTF8"))
(defparameter +bool-argument-ok+ '("ISNUM"))
(defparameter +aggregates+ '("ALL" "ANY" "MAP" "FILTER" "SUM" "JOIN"))
;;; The funcs whose SEL result has children, so the scalar rule does not apply.
;;; Also measured. Each is already refused by the dialect documents; the list
;;; exists so the aggregate-source path consults the map instead of falling
;;; through to the scalar branch, where COUNT and HAS folded to 0 and FALSE.
(defparameter +yields-list+ '("BTL" "INDEXES" "RGROUPS" "SPLIT"))

(defun regex-at (name)
  "Which funcs take a regex, and at which 0-based argument. All four name index
0; the shape exists so a function taking a regex elsewhere is one entry rather
than a code change."
  (when (member name '("RMATCH" "RFIND" "RREPLACE" "RGROUPS") :test #'equal) 0))

(defun require-argument-kind (name f pos)
  (when (and (eq (fragment-kind f) :bool)
             (not (member name +bool-argument-ok+ :test #'equal)))
    (refuse "E_SQL_SHAPE"
            (format nil "~a does not take a BOOL argument; SEL raises here rather ~
than reading a boolean as text or as 1" name)
            pos))
  (when (and (eq (fragment-kind f) :bin)
             (not (member name +bin-argument-ok+ :test #'equal)))
    (refuse "E_SQL_SHAPE"
            (format nil "~a reads its argument as text, and this is BIN; SEL ~
raises here rather than reinterpreting bytes as characters" name)
            pos)))

(defun rewrite-regex (n)
  (let* ((name (sel::node-s n))
         (at (regex-at name)))
    (unless at (return-from rewrite-regex n))
    (let* ((args (copy-list (sel::node-items n)))
           (pat (nth at args)))
      ;; Both the pattern and the flags must be literals: a pattern read from a
      ;; column cannot be rewritten, and the flag selects the template.
      (unless (and pat (eq (snode-kind pat) :text))
        (refuse "E_SQL_UNSUPPORTED"
                (format nil "~a needs a literal pattern here: SEL rewrites \\d, ~
\\w and \\s into explicit ASCII classes before matching, and a pattern that is ~
not known until the query runs cannot be rewritten" name)
                (if pat (snode-pos pat) (snode-pos n))))
      (let ((source
              (handler-case
                  ;; The language's OWN rewriter. A copy here would be a second
                  ;; thing to keep in step, and it would fail silently when they
                  ;; drifted.
                  (sel::validate-pattern (sel::node-s pat) (snode-pos pat) t nil)
                (sel:sel-error (e)
                  ;; SEL raises this too, but only when the call is REACHED.
                  ;; Translation walks every branch, so a pattern in a branch the
                  ;; evaluator never takes arrives here anyway -- and a SEL-ERROR
                  ;; escaping TRANSLATE would break the one thing TRY-TRANSLATE
                  ;; promises.
                  (refuse "E_SQL_UNSUPPORTED"
                          (format nil "~a's pattern is not in SEL's portable ~
subset, so there is nothing to translate: ~a" name (sel:sel-error-message e))
                          (snode-pos pat)))))
            ;; Dotall is permanently on in SEL (spec §7.8) and off by default in
            ;; the server, so every pattern carries (?s). The modifier goes in
            ;; the PATTERN rather than the template: selecting an arity-keyed
            ;; template by argument count gave every three-argument call the
            ;; case-insensitive form.
            (inline-flags "(?s)")
            (flag-at (if (equal name "RREPLACE") 3 2)))
        (when (>= flag-at (length args))
          (setf (nth at args) (lit-node :text (concatenate 'string inline-flags source)
                                        nil (snode-pos pat)))
          (return-from rewrite-regex (replace-items n args)))
        (let ((flags (nth flag-at args)))
          (unless (eq (snode-kind flags) :text)
            (refuse "E_SQL_UNSUPPORTED"
                    (format nil "~a needs literal flags here: their content ~
selects the mapping, so they have to be known before the query runs" name)
                    (snode-pos flags)))
          ;; The flag string's CONTENT chooses the template. Choosing by argument
          ;; count instead meant every three-argument call got the
          ;; case-insensitive form. Spelled as the two strings that pass rather
          ;; than as a case fold: naming them is byte-exact.
          (let ((text (sel::node-s flags)))
            (unless (member text '("" "i" "I") :test #'equal)
              (refuse "E_SQL_UNSUPPORTED"
                      (format nil "~a accepts only the i flag here, and SEL ~
accepts only i at all; ~s is not it" name text)
                      (snode-pos flags)))
            (when (plusp (length text))
              ;; The evaluator refuses i on a pattern with non-ASCII literals,
              ;; because case folding above ASCII is the one thing PCRE and
              ;; ECMAScript cannot be made to agree on.
              (when (some (lambda (c) (> (char-code c) #x7f)) source)
                (refuse "E_SQL_UNSUPPORTED"
                        "the i flag needs an ASCII-only pattern, which SEL ~
requires for the same reason and refuses here too"
                        (snode-pos flags)))
              (setf inline-flags "(?si)"))
            (setf (nth at args) (lit-node :text (concatenate 'string inline-flags source)
                                          nil (snode-pos pat)))
            (replace-items n (append (subseq args 0 flag-at) (subseq args (1+ flag-at))))))))))

(defun translate-call (tr n)
  ;; Captured before the rewrite, which preserves the name but rebinds the node.
  (let ((name (sel::node-s n)))
    ;; Each of these short-circuits before the next, and none reaches the funcs
    ;; table: the generator rejects a dialect document that lists one.
    (when (member name +aggregates+ :test #'equal)
      (return-from translate-call (translate-aggregate tr n)))
    (when (equal name "COUNT") (return-from translate-call (translate-count tr n)))
    (when (equal name "HAS") (return-from translate-call (translate-has tr n)))
    (when (equal name "INDEXES")
      (refuse "E_SQL_SHAPE"
              "INDEXES yields a list of keys, and a SQL expression is a scalar"
              (snode-pos n)))
    (when (equal name "ABORT")
      (refuse "E_SQL_UNSUPPORTED"
              "ABORT raises an error, which is a control-flow effect and not a ~
value a SQL expression can be" (snode-pos n)))
    (when (member name '("IF" "COND") :test #'equal)
      (return-from translate-call (translate-conditional tr n)))
    ;; Before any argument is rendered, so the deleted flag node never becomes a
    ;; parameter slot.
    (let* ((rewritten (rewrite-regex n))
           (args (loop for arg in (sel::node-items rewritten)
                       ;; Left to right, and the order is load-bearing: slot
                       ;; numbers are allocated in render order.
                       for f = (walk-node tr arg)
                       do (when (eq (fragment-kind f) :list)
                            (refuse "E_SQL_SHAPE"
                                    (format nil "argument to ~a is a list, and a ~
SQL expression is a scalar" name)
                                    (snode-pos arg)))
                          (require-argument-kind name f (snode-pos arg))
                       collect f)))
      ;; No variant is ever passed for funcs. SEL's own arity was enforced at
      ;; parse time, so APPLY-ENTRY defends only the dialect's narrowing.
      (apply-entry tr :funcs name args (snode-pos n)))))

;;; --- aggregates -----------------------------------------------------------

(defun agg-fold (name)
  (cond ((equal name "ALL") "AND") ((equal name "ANY") "OR")
        ((equal name "SUM") "+") (t nil)))     ; JOIN/MAP/FILTER fold otherwise

(defun agg-skeleton (name)
  (cond ((equal name "ALL") "all") ((equal name "ANY") "any")
        ((equal name "SUM") "sum") ((equal name "JOIN") "join") (t nil)))

(defun agg-returns (name)
  (cond ((member name '("ALL" "ANY") :test #'equal) :bool)
        ((equal name "SUM") :num) ((equal name "JOIN") :text) (t :list)))

(defun agg-shape (n)
  "The 2- and 3-argument forms: `_` by default, a bare name when given."
  (let ((args (sel::node-items n)))
    (if (= (length args) 3)
        (progn
          ;; BOTH halves of IS-BINDER-NAME matter: (C) parses as a var carrying
          ;; the parser's GROUPED flag, and the evaluator refuses it with
          ;; E_EXPECT_SYMBOL; testing only the kind accepted a binder the
          ;; language rejects, in all three hosts.
          (unless (is-binder-name (second args))
            (refuse "E_SQL_SHAPE"
                    (format nil "the binder of ~a must be a bare name" (sel::node-s n))
                    (snode-pos (second args))))
          (values (sel::node-s (second args)) (third args)))
        (values "_" (second args)))))

(defun value-node (tr v spec pos)
  "Synthesise one AST node from one value child. A value binding holds values,
not AST nodes, so nodes are manufactured -- deliberately cheaper than a fourth
binder shape, and it inherits the quoting decision from DECLARED-KIND rather
than restating it."
  (declare (ignore tr))
  (cond
    ((plusp (sel:value-size v))
     (let ((cl (make-clist pos)))
       (setf (clist-entries cl)
             (mapcar (lambda (cell) (cons (car cell) (value-node nil (cdr cell) spec pos)))
                     (sel:value-entries v)))
       cl))
    ((sel:value-bool-p v) (lit-node :bool "" (sel:as-bool v pos) pos))
    ((sel:value-bin-p v)
     (refuse "E_SQL_SHAPE"
             "a BIN element of a value binding has no literal node to become; ~
bind it as a column, or convert it before translating" pos))
    (t (lit-node (if (eq (getf spec :type) :num) :num :text) (sel:as-text v pos) nil pos))))

(defun value-elements (tr spec pos)
  (let ((v (getf spec :value)))
    (if (zerop (sel:value-size v))
        ;; A NONE with no children is genuinely empty -- what FILTER returns when
        ;; nothing matched. A scalar is a one-element list of itself.
        (if (sel:value-none-p v) '() (list (cons "1" (binder-node (value-node tr v spec pos)))))
        (mapcar (lambda (cell)
                  (cons (car cell) (binder-node (value-node tr (cdr cell) spec pos))))
                (sel:value-entries v)))))

(defstruct (source (:constructor %source (shape elements relation filters scalar-rule)))
  (shape :static) (elements '()) (relation nil) (filters '()) (scalar-rule nil))

(defun classify (tr src)
  "THE SHAPE CLASSIFIER. Branch order is exactly the other hosts'."
  (when (eq (snode-kind src) :call)
    (let ((name (sel::node-s src)))
      (when (equal name "FILTER")
        ;; Refuses FIRST if the FILTER's binder is not a bare name, then recurses
        ;; so nested FILTERs conjoin -- innermost first.
        (multiple-value-bind (fb fbody) (agg-shape src)
          (let ((inner (classify tr (first (sel::node-items src)))))
            (setf (source-filters inner)
                  (append (source-filters inner) (list (cons fb fbody))))
            (return-from classify inner))))
      (when (equal name "MAP")
        (refuse "E_SQL_UNSUPPORTED"
                "MAP as the thing an aggregate iterates is not translated: unlike ~
FILTER, which only decides whether an element takes part, MAP changes what the ~
element is, so the two binders mean different things and binding both to one ~
element is not enough. See docs/SQL-TRANSLATION.md 7.5"
                (snode-pos src)))))
  (case (snode-kind src)
    ;; RETURN-FROM, not a CASE value: the branches below fall through to the
    ;; scalar rule, and a branch that merely evaluated to a source would build
    ;; one and throw it away -- which made ALL((1,2,3), _ > 0) refuse and
    ;; COUNT((1,2,3)) answer 0.
    (:list (return-from classify
             (%source :static
                      (loop for item in (sel::node-items src)
                            for i from 1
                            collect (cons (format nil "~D" i) (binder-node item)))
                      nil '() nil)))
    (:clist (return-from classify
              (%source :static
                       (mapcar (lambda (c) (cons (car c) (binder-node (cdr c))))
                               (clist-entries src))
                       nil '() nil)))
    (:var
     (let ((bound (find-binder tr (sel::node-s src))))
       (when bound
         (case (binder-shape bound)
           (:node (return-from classify (classify tr (binder-payload bound))))
           (:none (refuse "E_SQL_SHAPE" (binder-reason bound) (snode-pos src)))
           (:row (when (> (length (getf (binder-payload bound) :fields)) 1)
                   (refuse "E_SQL_SHAPE"
                           (format nil "~a is a row of a multi-field relation, ~
which is a map with one child per field; SQL has no way to iterate or count that"
                                   (sel::node-s src))
                           (snode-pos src)))))
         ;; A :COLUMN, or a :ROW with exactly one field: the scalar rule.
         (return-from classify (%source :static (list (cons "1" bound)) nil '() t)))
       (let* ((b (bindings-get (translator-bindings tr) (sel::node-s src) (snode-pos src)))
              (spec (binding-spec b)))
         (case (binding-kind b)
           (:relation (return-from classify (%source :relation '() spec '() nil)))
           (:columns (return-from classify
                       (%source :columns
                                (loop for item in (getf spec :items)
                                      for i from 1
                                      collect (cons (format nil "~D" i) (binder-column item)))
                                nil '() nil)))
           (:value
            (let ((v (getf spec :value)))
              (return-from classify
                (%source :static (value-elements tr spec (snode-pos src)) nil '()
                         ;; A scalar value gets the scalar rule; a list does not,
                         ;; and an empty NONE gets no elements and no scalar rule.
                         (and (zerop (sel:value-size v)) (not (sel:value-none-p v)))))))
           ;; A plain `column` binding falls through, exactly as in Python.
           (t nil)))))
    (t nil))
  (when (and (eq (snode-kind src) :call)
             (member (sel::node-s src) +yields-list+ :test #'equal))
    (refuse "E_SQL_SHAPE"
            (format nil "~a yields a list, and the scalar rule does not apply to ~
it; SQL has no way to count or index what it produces" (sel::node-s src))
            (snode-pos src)))
  ;; The scalar rule for any other expression node.
  (%source :static (list (cons "1" (binder-node src))) nil '() t))

(defun frame-set (frame name b)
  "Assign, never insert-if-absent: the write ORDER is semantics. If the
aggregate's binder is itself named `_K`, the later `_K` write wins and `_K`
resolves to the key -- which is what the evaluator does."
  (let ((cell (assoc name frame :test #'equal)))
    (if cell (progn (setf (cdr cell) b) frame) (append frame (list (cons name b))))))

(defun with-element (tr src binder-name elem key n render)
  (let ((frame '()))
    (setf frame (frame-set frame binder-name elem))
    ;; `_K` names a TEXT literal of the element's key, which becomes a parameter
    ;; slot when rendered.
    (setf frame (frame-set frame "_K" (binder-node (lit-node :text key nil (snode-pos n)))))
    ;; Every absorbed FILTER's binder names the SAME element, which is what makes
    ;; absorption three lines rather than a substitution pass.
    (dolist (f (source-filters src)) (setf frame (frame-set frame (car f) elem)))
    (push frame (translator-frames tr))
    (unwind-protect (funcall render) (pop (translator-frames tr)))))

(defun with-row (tr src binder-name render)
  (let ((alias (relation-alias (source-relation src))))
    ;; A relation nested inside itself reuses its own fixed alias, and the inner
    ;; FROM shadows the outer one, so the predicate is constantly false and every
    ;; server answered [] where SEL answers rows. CHECK-ALIASES dedupes across
    ;; DISTINCT binding names, and this is one name, so it could never fire.
    (dolist (frame (translator-frames tr))
      (dolist (cell frame)
        (let ((b (cdr cell)))
          (when (and (eq (binder-shape b) :row)
                     (equal (relation-alias (binder-payload b)) alias))
            ;; No position: the source record carries none in any host, so this
            ;; reports 0:0. Reproduced rather than improved.
            (refuse "E_SQL_SHAPE"
                    (format nil "this relation is already open as ~a further out, ~
and a subquery reusing its own alias shadows the outer row rather than comparing ~
against it; the correlation names the alias, so it cannot be renamed here" alias))))))
    (let ((row (binder-row (source-relation src)))
          (frame '()))
      (setf frame (frame-set frame binder-name row))
      (setf frame (frame-set frame "_K"
                             (binder-none "a row of a relation has no key: SQL rows ~
are unordered and unkeyed unless the schema says otherwise, and guessing which ~
column is the key is not something this layer does")))
      (dolist (f (source-filters src)) (setf frame (frame-set frame (car f) row)))
      (push frame (translator-frames tr))
      (unwind-protect (funcall render) (pop (translator-frames tr))))))

(defun agg-body (tr name body src n)
  (let ((q (walk-node tr body)))
    (if (equal name "SUM")
        (require-num q (snode-pos body) name)
        (require-bool q (snode-pos body) name))
    ;; Filters are innermost-first and each wraps the accumulator, so the
    ;; OUTERMOST filter ends up the OUTERMOST wrapper.
    ;;
    ;; The body is rendered, and its literals numbered, BEFORE each predicate --
    ;; but every rewrite EMITS the predicate first. Slot ids are creation-ordered
    ;; and BINDINGS walks the text, so the two orders differ here by construction.
    (dolist (f (source-filters src) q)
      (let ((p (require-bool (walk-node tr (cdr f)) (snode-pos (cdr f)) "FILTER")))
        (setf q
              (cond
                ((equal name "SUM")
                 (case-when tr p q (make-literal tr (sel:make-num "0") :num) (snode-pos n)))
                ;; (NOT p) OR q, and not an implication or a CASE: the exact
                ;; shape is what makes it NULL-safe.
                ((equal name "ALL")
                 (apply-entry tr :ops "OR"
                              (list (apply-entry tr :ops "NOT" (list p) (snode-pos n)) q)
                              (snode-pos n)))
                (t (apply-entry tr :ops "AND" (list p q) (snode-pos n)))))))))

(defun relation-aggregate (tr name rel body n)
  (%fragment (fill-named tr (skeleton tr (agg-skeleton name) (snode-pos n))
                         (merge-slots (relation-slots tr rel)
                                      (list (cons "body" (list body))))
                         (snode-pos n))
             (agg-returns name) (translator-dialect tr)))

(defun translate-aggregate (tr n)
  (let ((name (sel::node-s n)))
    (when (member name '("MAP" "FILTER") :test #'equal)
      (refuse "E_SQL_SHAPE"
              (format nil "~a yields a list, and a SQL expression is a scalar; it ~
can only be the thing another aggregate iterates" name)
              (snode-pos n)))
    (when (equal name "JOIN") (return-from translate-aggregate (translate-join tr n)))
    ;; Refuses a non-bare binder BEFORE the source is classified, so
    ;; ALL(UNBOUND, (C), p) is E_SQL_SHAPE and not E_SQL_UNBOUND.
    (multiple-value-bind (binder-name body) (agg-shape n)
      (let ((src (classify tr (first (sel::node-items n)))))
        (when (eq (source-shape src) :relation)
          (return-from translate-aggregate
            (relation-aggregate tr name (source-relation src)
                                (with-row tr src binder-name
                                          (lambda () (agg-body tr name body src n)))
                                n)))
        (let ((parts (loop for cell in (source-elements src)
                           collect (with-element tr src binder-name (cdr cell) (car cell) n
                                                 (lambda () (agg-body tr name body src n))))))
          (cond
            ;; Spec §7.3's empty cases.
            ((null parts)
             (cond ((equal name "ALL") (make-literal tr (sel:make-bool t) :bool))
                   ((equal name "ANY") (make-literal tr (sel:make-bool nil) :bool))
                   (t (make-literal tr (sel:make-num "0") :num))))
            ;; Unwrapped: no fold and no parentheses. Always folding would emit
            ;; extra parentheses and break the byte-exact cases.
            ((null (rest parts)) (first parts))
            (t (fold-pairwise tr (agg-fold name) parts (snode-pos n)))))))))

(defun translate-count (tr n)
  (let ((src (classify tr (first (sel::node-items n)))))
    ;; THE SCALAR RULE IS INVERTED FOR COUNT: spec §7.4 says a value with no
    ;; children counts 0, where §7.3's one-element rule is about what an
    ;; aggregate ITERATES.
    (when (and (not (eq (source-shape src) :relation))
               (source-scalar-rule src)
               (null (source-filters src)))
      (return-from translate-count (make-literal tr (sel:make-num "0") :num)))
    (when (source-filters src)
      ;; COUNT(FILTER(L, p)) is SUM(L, CASE WHEN p THEN 1 ELSE 0 END).
      (let ((body (lit-node :num "1" nil (snode-pos n))))
        (when (eq (source-shape src) :relation)
          (return-from translate-count
            (relation-aggregate tr "SUM" (source-relation src)
                                (with-row tr src "_"
                                          (lambda () (agg-body tr "SUM" body src n)))
                                n)))
        (let ((parts (loop for cell in (source-elements src)
                           collect (with-element tr src "_" (cdr cell) (car cell) n
                                                 (lambda () (agg-body tr "SUM" body src n))))))
          (return-from translate-count
            (cond ((null parts) (make-literal tr (sel:make-num "0") :num))
                  ((null (rest parts)) (first parts))
                  (t (fold-pairwise tr "+" parts (snode-pos n))))))))
    (when (eq (source-shape src) :relation)
      ;; The count skeleton takes only {from} and {corr} -- no {body} -- so there
      ;; is nothing to merge. This path pushes NO frame, and so skips WITH-ROW's
      ;; self-nesting check: an asymmetry the other hosts have too.
      (return-from translate-count
        (%fragment (fill-named tr (skeleton tr "count" (snode-pos n))
                               (relation-slots tr (source-relation src)) (snode-pos n))
                   :num (translator-dialect tr))))
    ;; Decided at translation time.
    ;;
    ;; ~D, not PRINC-TO-STRING. Under a rebound *PRINT-BASE* the latter renders
    ;; 12 as "C", MAKE-NUM then raises E_NOT_NUM, and that SEL-ERROR escapes
    ;; TRY-TRANSLATE, which catches only SQL-ERROR.
    (make-literal tr (sel:make-num (format nil "~D" (length (source-elements src)))) :num)))

(defun translate-has (tr n)
  (let ((key-node (second (sel::node-items n))))
    ;; Runs BEFORE the source is classified, so HAS(UNBOUND, SOMEVAR) is
    ;; E_SQL_SHAPE and not E_SQL_UNBOUND.
    (unless (member (snode-kind key-node) '(:text :num))
      (refuse "E_SQL_SHAPE"
              "HAS needs a constant key here: which column it asks about has to ~
be known before the query runs" (snode-pos key-node)))
    (let ((key (sel::node-s key-node))
          (src (classify tr (first (sel::node-items n)))))
      (when (source-filters src)
        (refuse "E_SQL_SHAPE"
                "HAS over a FILTER would have to know at translation time which ~
elements the filter kept" (snode-pos n)))
      (when (eq (source-shape src) :relation)
        (refuse "E_SQL_SHAPE"
                "HAS over a relation asks whether it has a key, and a relation is ~
a list of rows whose keys are positions; the answer needs the row count, which no ~
expression here knows" (snode-pos n)))
      ;; A scalar always answers FALSE -- a scalar has no children -- even though
      ;; its element map carries the synthetic key "1". Exact string match.
      (make-literal tr (sel:make-bool (and (not (source-scalar-rule src))
                                           (assoc key (source-elements src) :test #'equal)
                                           t))
                    :bool))))

(defun translate-join (tr n)
  (let ((src (classify tr (first (sel::node-items n))))
        (sep-node (second (sel::node-items n))))
    (when (eq (source-shape src) :relation)
      (let* ((rel (source-relation src))
             (scalar (getf rel :scalar))
             (cell (and scalar (assoc (sel::ascii-upcase scalar) (getf rel :fields)
                                      :test #'equal))))
        ;; Before the skeleton, so a no-scalar relation reports this rather than
        ;; the dialect's join refusal.
        (unless cell
          (refuse "E_SQL_SHAPE"
                  "JOIN over a relation needs the binding to name a \"scalar\" field"
                  (snode-pos n)))
        (let ((body (column-ref tr (cdr cell)))
              ;; The separator is rendered only after the skeleton is known to
              ;; exist.
              (skel (skeleton tr "join" (snode-pos n))))
          (return-from translate-join
            (%fragment (fill-named tr skel
                                   (merge-slots (relation-slots tr rel)
                                                (list (cons "body" (list body))
                                                      (cons "sep" (list (walk-node tr sep-node)))))
                                   (snode-pos n))
                       :text (translator-dialect tr))))))
    (let ((parts '()))
      (dolist (cell (source-elements src))
        ;; Rendered afresh per gap, never spliced twice: N-1 separators means N-1
        ;; identical bound values, which is correct.
        (when parts (push (walk-node tr sep-node) parts))
        (let ((held (cdr cell)))
          (push (with-element tr src "_" held (car cell) n
                              (lambda () (from-binder tr held n)))
                parts)))
      (setf parts (nreverse parts))
      (cond ((null parts) (make-literal tr (sel:make-text "") :text))
            ((null (rest parts)) (first parts))
            ;; The dialect's own concatenation template, pairwise-left, because
            ;; `&` is what SEL's JOIN is.
            (t (fold-pairwise tr "&" parts (snode-pos n)))))))

;;; --- IN -------------------------------------------------------------------

(defun translate-in (tr n)
  (let* ((rhs (sel::node-r n))
         (d (translator-dialect tr))
         (free-var (and (eq (snode-kind rhs) :var)
                        (null (find-binder tr (sel::node-s rhs))))))
    ;; --- branch A: a relation
    (when (and free-var (bindings-has (translator-bindings tr) (sel::node-s rhs)))
      (let ((b (bindings-get (translator-bindings tr) (sel::node-s rhs) (snode-pos rhs))))
        (when (eq (binding-kind b) :relation)
          (let* ((rel (binding-spec b))
                 (scalar (getf rel :scalar))
                 (fields (getf rel :fields))
                 (cell (and scalar (assoc (sel::ascii-upcase scalar) fields :test #'equal))))
            ;; Fires BEFORE the field count, so a multi-field relation with no
            ;; scalar gets this message.
            (unless cell
              (refuse "E_SQL_SHAPE"
                      (format nil "IN over ~a needs the binding to name a ~
\"scalar\" field: that is the column the subquery projects" (sel::node-s rhs))
                      (snode-pos rhs)))
            (unless (= (length fields) 1)
              (refuse "E_SQL_SHAPE"
                      (format nil "IN over ~a is refused: the relation declares ~a ~
fields, so SEL reads its rows as maps and a scalar can never equal one. Bind the ~
projected column as a relation with that one field." (sel::node-s rhs) (length fields))
                      (snode-pos rhs)))
            ;; The skeleton FIRST, before the needle is rendered. The dynamic
            ;; hosts get this order from left-to-right argument evaluation; a
            ;; statement that built the slots first would render the needle --
            ;; and could refuse from inside it -- before discovering the dialect
            ;; cannot express inRelation at all.
            (let ((skel (skeleton tr "inRelation" (snode-pos n))))
              (return-from translate-in
                (%fragment
                 (fill-named tr skel
                             (merge-slots
                              (relation-slots tr rel)
                              ;; No REQUIRE-COMPARABLE-KINDS here, and
                              ;; TEXT-OPERAND is applied unconditionally -- there
                              ;; is no two-BIN skip as in TRANSLATE-BINARY.
                              (list (cons "needle" (list (emit-text-operand
                                                          d (walk-node tr (sel::node-l n)))))
                                    (cons "body" (list (emit-text-operand
                                                        d (column-ref tr (cdr cell)))))))
                             (snode-pos n))
                 :bool d)))))))
    ;; --- element collection. NIL and an empty list are DIFFERENT states: no
    ;; list shape recognised means the scalar fallback, an empty list means FALSE.
    (let ((elements :none))
      (case (snode-kind rhs)
        (:list (setf elements (sel::node-items rhs)))
        (:clist (setf elements (mapcar #'cdr (clist-entries rhs))))   ; keys discarded
        (t (when (and free-var (bindings-has (translator-bindings tr) (sel::node-s rhs)))
             (let* ((b (bindings-get (translator-bindings tr) (sel::node-s rhs) (snode-pos rhs)))
                    (spec (binding-spec b)))
               (when (and (eq (binding-kind b) :value)
                          (plusp (sel:value-size (getf spec :value))))
                 (setf elements (mapcar (lambda (c) (binder-payload (cdr c)))
                                        (value-elements tr spec (snode-pos rhs)))))))))
      ;; --- branch B: the scalar fallback
      (when (eq elements :none)
        (let* ((r (walk-node tr rhs))                 ; RIGHT operand rendered FIRST
               (l (walk-node tr (sel::node-l n))))
          (require-comparable-kinds l r "IN" (snode-pos n))
          (return-from translate-in
            (apply-entry tr :ops "IN"
                         (list (emit-text-operand d l) (emit-text-operand d r))
                         (snode-pos n) "scalar"))))
      ;; --- branch C: an empty list
      (when (null elements)
        (return-from translate-in (make-literal tr (sel:make-bool nil) :bool)))
      ;; --- branch D: a list of elements
      (let ((tests '()))
        (dolist (e elements)
          ;; Re-rendered once per element, which is not an optimisation to
          ;; remove: splicing one fragment N times puts the same slot number in
          ;; the output N times while PARAMS holds one entry.
          (let* ((raw (walk-node tr (sel::node-l n)))
                 (needle (emit-text-operand d raw))
                 (f (walk-node tr e)))
            (when (eq (fragment-kind f) :list)
              (refuse "E_SQL_SHAPE"
                      "IN over a list of lists is structural in SEL and has no ~
SQL counterpart" (snode-pos e)))
            ;; RAW, not NEEDLE: needle's kind is always TEXT after the cast.
            (require-comparable-kinds raw f "IN" (snode-pos e))
            ;; The map key is EQL with variant text; the IN entry is not used here.
            (push (apply-entry tr :ops "EQL" (list needle (emit-text-operand d f))
                               (snode-pos e) "text")
                  tests)))
        (fold-pairwise tr "OR" (nreverse tests) (snode-pos n))))))

;;; --- the public interface -------------------------------------------------

(defun translate (program dialect &optional bindings options)
  "Translate a compiled program into a SQL expression for one dialect.

Signals SQL-ERROR, whose message is written to be READ. Use this when you want
to know why a rule cannot be pushed down."
  ;; BEFORE the struct, not after. TRANSLATOR's DIALECT slot is declared
  ;; :type string and SBCL enforces that at construction, so a non-string
  ;; dialect signalled a raw TYPE-ERROR from inside %TRANSLATOR -- escaping
  ;; TRY-TRANSLATE, which catches only SQL-ERROR -- before REQUIRE-TARGET, whose
  ;; whole job is to turn a bad dialect name into a graceful refusal, ever ran.
  ;; The slot type stays: it is a real invariant, and this moves its enforcement
  ;; to after the refusal rather than removing it.
  ;;
  ;; The order below is contract too: a caller with both a bad dialect and a
  ;; duplicate alias gets E_SQL_DIALECT, so those two must not be fused either.
  (require-target dialect)
  (let ((tr (%translator dialect (make-bindings (or bindings '()))
                         (and (getf options :strict) t))))
    (bindings-check-aliases (translator-bindings tr))
    (multiple-value-bind (names root) (const-scope (translator-bindings tr))
      (setf (translator-const-names tr) names
            (translator-const-root tr) root)
      (let ((f (walk-node tr (normalise (sel:program-ast program) names root))))
        ;; Only this final fragment carries the vectors; every intermediate one
        ;; built during the walk has none.
        (%fragment (fragment-parts f) (fragment-kind f) dialect
                   (reverse (translator-params tr))
                   (reverse (translator-param-kinds tr))
                   (reverse (translator-caveats tr)))))))

(defun try-translate (program dialect &optional bindings options)
  "The same, returning NIL instead of signalling.

Only SQL-ERROR is caught: a bug in the translator, or a malformed registration,
must not be swallowed by the path that exists to handle refusals."
  (handler-case (translate program dialect bindings options)
    (sql-error () nil)))

(defun dialects () (dialect-targets))
