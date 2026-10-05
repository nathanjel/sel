;;;; Precedence climbing. The sixteen levels of spec/SPEC.md §5 are the table
;;;; below rather than sixteen functions, so adding an operator is adding a row.
;;;; python/sel/parser.py was the pilot of this shape (no host is the reference:
;;;; spec/ and conformance/ are) and its module docstring is the rationale; docs/contributing.md, "Adding an operator",
;;;; step 5, records what every host had to get right, each item of which
;;;; produces a valid parse of the WRONG TREE when it is wrong.
;;;;
;;;; `;` and `,` stay hand-written N-ary loops outside the table, because they
;;;; build N-ary nodes rather than binary ones -- DEPENDENCIES walks `items`, and
;;;; PARSE-CALL flattens a top-level list into the argument vector.

(in-package #:sel)


(defstruct (node (:constructor make-node (kind pos)))
  ;; :num :text :bool :null :var :index :seq :list :un :bin :assign :call
  (kind :num :type keyword)
  (pos nil)
  (s "" :type string)      ; num/text literal, var name, or operator
  (b nil)                  ; :bool value
  (grouped nil)            ; came from ( ), so F((1,2)) passes one list not two args
  (l nil)                  ; bin/index/assign left; un operand
  (r nil)                  ; bin/index/assign right
  (items nil :type list)   ; seq/list items, call arguments
  ;; Prepared lazily on the final call node. The optimizer's explicit shallow
  ;; copy omits this slot; replacing an argument list also invalidates it.
  ;; Keep (source-list . vector) together so readers see one coherent snapshot.
  (argument-plan nil)
  (spec nil)               ; call
  (record-shape nil)       ; prepared literal-key RECORD layout
  (dec-val nil)            ; cached DEC struct for :num nodes
  (math-plan nil)          ; compiled MathPlan when physical AST is optimized
  ;; On a FILTER body: whether the step after the FILTER renumbers without
  ;; reading `_K`, so nothing observes the keys its result carries and the
  ;; evaluator's join pre-filter may drop rows below the join (SEL-0052).
  (keys-unobserved nil)
  ;; A :bin node's operator as a keyword, filled on first evaluation (EVAL's
  ;; BINARY-OP-CODE) so the evaluator dispatches with CASE instead of a chain of
  ;; STRING= on every evaluation. Never copied: a copy re-derives it.
  (opc nil)
  ;; On a :var node the hybrid planner builds: this read is of the variable's
  ;; BINDING (the relation, or the rows a SQL prefix returned), even where a
  ;; leading helper assignment has the same name -- `ORDERS = ORDERS .> DROP(2)`
  ;; unwound into the pipeline. Stage 1 never inlines a helper into it, and it
  ;; does not make the planner carry that helper. The evaluator ignores it.
  (binding-read nil))

(declaim (inline shipped-call-p))
(defun shipped-call-p (node)
  "Whether the call NODE names a function the library ships -- the one answer to
\"may this call do something other than return a value?\" (an application's
function may write into its argument, or anything else). Read from the spec the
node was compiled with, so it costs no table lookup."
  (let ((spec (node-spec node)))
    (and spec (spec-shipped spec))))

;;; The operator families, named once for the PARSER: the precedence table below
;;; is BUILT from these rather than repeating them. +compare-ops+ holds both the
;;; numeric and the `$` text comparisons, all at one binding power. The
;;; evaluator does not read these lists; it dispatches on the keyword
;;; BINARY-OP-CODE (eval.lisp) gives each operator, cached per node by
;;; NODE-OP-CODE.
(defparameter +assign-ops+ '("=" "+=" "-=" "*=" "/=" "%=" "&="))
(defparameter +compare-ops+
  '("==" "!=" "<" "<=" ">" ">=" "$==" "$!=" "$<" "$<=" "$>" "$>="))
(defparameter +compare-words+ '("EQL" "IN"))

;;; spec/SPEC.md §5, as a table. Higher binds tighter. The gaps are the levels
;;; that are not infix: 16 is postfix/primary, 15 is unary minus, 7 is NOT.
;;; +BP-SEQ+ and +BP-LIST+ are never read -- `;` and `,` are N-ary loops outside
;;; the table -- and are here because a table missing two of §5's sixteen levels
;;; stops being a reading of §5.
(defconstant +bp-seq+ 1)       ; ;
(defconstant +bp-list+ 2)      ; ,
(defconstant +bp-assign+ 3)    ; = += -= *= /= %= &=   (right associative)
(defconstant +bp-or+ 4)
(defconstant +bp-xor+ 5)
(defconstant +bp-and+ 6)
(defconstant +bp-not+ 7)       ; prefix
(defconstant +bp-compare+ 8)   ; non-associative
(defconstant +bp-coalesce+ 9)  ; ?? ??? (right associative)
(defconstant +bp-bor+ 10)
(defconstant +bp-bxor+ 11)
(defconstant +bp-band+ 12)
(defconstant +bp-concat+ 13)   ; &
(defconstant +bp-add+ 14)      ; + -
(defconstant +bp-mul+ 15)      ; * / %
(defconstant +bp-neg+ 16)      ; prefix

;;; An infix operator's binding power and associativity, as (BP . ASSOC) where
;;; ASSOC is #\L, #\R or #\N. #\L parses its right side at BP + 1, #\R at BP --
;;; that is what makes it right-associative -- and #\N at BP + 1 and then rejects
;;; a second operator at the same level.
;;;
;;; DEFPARAMETER rather than DEFCONSTANT, following the +assign-ops+ style above:
;;; DEFCONSTANT on a fresh hash table signals on every reload.
;;;
;;; :test #'equal is mandatory. The keys are strings, and EQL never matches two
;;; separately-read strings, so with the default test every operator would look
;;; unknown and every program would be a syntax error at its first operator.
(defparameter +infix-ops+
  (let ((m (make-hash-table :test #'equal)))
    (setf (gethash "??" m) (cons +bp-coalesce+ #\R)
          (gethash "???" m) (cons +bp-coalesce+ #\R)
          (gethash "&" m) (cons +bp-concat+ #\L)
          (gethash "+" m) (cons +bp-add+ #\L)
          (gethash "-" m) (cons +bp-add+ #\L)
          (gethash "*" m) (cons +bp-mul+ #\L)
          (gethash "/" m) (cons +bp-mul+ #\L)
          (gethash "%" m) (cons +bp-mul+ #\L))
    (dolist (op +assign-ops+) (setf (gethash op m) (cons +bp-assign+ #\R)))
    (dolist (op +compare-ops+) (setf (gethash op m) (cons +bp-compare+ #\N)))
    m))

(defparameter +infix-words+
  (let ((m (make-hash-table :test #'equal)))
    (setf (gethash "OR" m) (cons +bp-or+ #\L)
          (gethash "XOR" m) (cons +bp-xor+ #\L)
          (gethash "AND" m) (cons +bp-and+ #\L)
          (gethash "BOR" m) (cons +bp-bor+ #\L)
          (gethash "BXOR" m) (cons +bp-bxor+ #\L)
          (gethash "BAND" m) (cons +bp-band+ #\L))
    (dolist (w +compare-words+) (setf (gethash w m) (cons +bp-compare+ #\N)))
    m))

;;; The two tables are one lookup. Every question about an operator -- what it
;;; binds at, how it associates, and whether it may follow a comparison -- is
;;; answered from here, so adding an operator really is adding a row. Two tables
;;; and not one because word operators lex as identifiers and symbol operators as
;;; ops, so they cannot share a key space; the binding powers are one scale. NIL
;;; for a token that is not an infix operator, which is the same answer as "stop".
(defun infix-entry (tok)
  (case (token-type tok)
    (:op (gethash (token-value tok) +infix-ops+))
    (:ident (gethash (token-value tok) +infix-words+))
    (t nil)))

(defun describe-token (tok)
  (case (token-type tok)
    (:eof "end of input")
    (:text "a text literal")
    (:num (format nil "number ~a" (token-value tok)))
    (t (format nil "~s" (token-value tok)))))

(defun finish-call (name-tok spec args)
  "The compile-time arity rule (spec 6.2, SEL-0002) and the call node, in one
place for both call forms; the pipeline form has already placed its left
operand in ARGS. Every refusal reports the name token."
  (let ((count (length args)))
    (when (or (< count (spec-min spec)) (> count (spec-max spec)))
      (fail "E_ARITY"
            (format nil "~a takes ~a, got ~d" (spec-name spec) (arity-text spec) count)
            (token-pos name-tok)))
    (when (spec-arity-error spec)
      (let ((problem (funcall (spec-arity-error spec) count)))
        (when problem (fail "E_ARITY" problem (token-pos name-tok)))))
    (check-literal-regex-pattern spec args)
    (let ((n (make-node :call (token-pos name-tok))))
      (setf (node-s n) (spec-name spec)
            (node-spec n) spec
            (node-items n) args
            (node-record-shape n) (prepare-record-shape n))
      n)))

(defun regex-flag-index (name)
  "The argument index of a regex builtin's flags -- its pattern is argument 0
-- or NIL when NAME takes no regex. The one list of them: the compile-time
pattern check here, the evaluator's builtins and the SQL translator read it."
  (cond ((member name '("RMATCH" "RFIND" "RGROUPS") :test #'string=) 2)
        ((string= name "RREPLACE") 3)))

(defun check-literal-regex-pattern (spec args)
  "A regex call whose pattern is a text literal is checked when the program is
compiled (SPEC 7.8), not when the call happens to run: `IF(FALSE, RMATCH('(?=a)',
s), 1)` is refused, so a rule's validity never depends on which branch its data
takes. A literal flags argument is checked with it; any other flags argument is
left to the run."
  (let ((name (spec-name spec)))
    (let ((flag-index (regex-flag-index name)))
     (when flag-index
      (let* ((pattern (first args))
             (flags (nth flag-index args)))
        (when (and pattern (eq (node-kind pattern) :text))
          (funcall 'regex-literal-check (node-s pattern)
                   (and flags (eq (node-kind flags) :text) (node-s flags))
                   (node-pos pattern)
                   (if flags (node-pos flags) (node-pos pattern)))))))))

(defun arity-text (spec)
  (let ((plural (if (= (spec-min spec) 1) "" "s")))
    (cond ((>= (spec-max spec) +variadic+)
           (format nil "at least ~d argument~a" (spec-min spec) plural))
          ((= (spec-min spec) (spec-max spec))
           (format nil "~d argument~a" (spec-min spec) plural))
          (t (format nil "~d to ~d arguments" (spec-min spec) (spec-max spec))))))

(defun prepare-record-shape (node)
  (let ((items (node-items node)))
    (when (and (string= (node-s node) "RECORD") items (evenp (length items))
               (loop for tail on items by #'cddr
                     always (eq (node-kind (first tail)) :text)))
      (let ((keys (loop for tail on items by #'cddr collect (node-s (first tail)))))
        (when (keys-distinct-p keys)
          (get-record-shape keys))))))

(defstruct (parser (:constructor %make-parser (toks)))
  (toks #() :type vector)
  (i 0 :type fixnum)
  (depth 0 :type fixnum))

(defun p-peek (p) (aref (parser-toks p) (parser-i p)))
(defun p-next (p) (prog1 (aref (parser-toks p) (parser-i p)) (incf (parser-i p))))
(defun p-at-op (p v)
  (let ((tok (p-peek p)))
    (and (eq (token-type tok) :op) (string= (token-value tok) v))))
(defun p-at-eof (p) (eq (token-type (p-peek p)) :eof))

(defun p-expect-op (p v)
  (unless (p-at-op p v)
    (let ((tok (p-peek p)))
      (fail "E_SYNTAX" (format nil "expected ~s, got ~a" v (describe-token tok))
            (token-pos tok))))
  (p-next p))

(defmacro with-depth ((p pos) &body body)
  `(progn
     (incf (parser-depth ,p))
     (when (> (parser-depth ,p) +max-depth+)
       (fail "E_DEPTH" "expression nested too deeply" ,pos))
     (unwind-protect (progn ,@body)
       (decf (parser-depth ,p)))))

(defun parse-source (source)
  (let ((p (%make-parser (coerce (tokenize source) 'vector))))
    (parse-program p)))

(defun parse-program (p)
  (let ((node (parse-sequence p)))
    (unless (p-at-eof p)
      (let ((tok (p-peek p)))
        (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok)) (token-pos tok))))
    node))

;;; sequence = list { ";" list } [ ";" ]
(defun parse-sequence (p)
  (with-depth (p (token-pos (p-peek p)))
    (let ((items (list (parse-list p))))
      (loop while (p-at-op p ";")
            do (p-next p)
               ;; A trailing ';' before a closer or end of input is permitted.
               (when (or (p-at-eof p) (p-at-op p ")") (p-at-op p "]")) (return))
               (push (parse-list p) items))
      (setf items (nreverse items))
      (if (= (length items) 1)
          (first items)
          (let ((n (make-node :seq (node-pos (first items)))))
            (setf (node-items n) items)
            n)))))

;;; list = term { "," term }
(defun parse-list (p)
  (let ((items (list (parse-term p +bp-assign+))))
    (loop while (p-at-op p ",")
          do (p-next p)
             (push (parse-term p +bp-assign+) items))
    (setf items (nreverse items))
    (if (= (length items) 1)
        (first items)
        (let ((n (make-node :list (node-pos (first items)))))
          (setf (node-items n) items)
          n))))

;;; MAKE-NODE takes only (kind pos), so without these two the three branches of
;;; PARSE-TERM would be several times wordier than the same code in every other
;;; host, and the shape they share would stop being visible.
(defun bin-node (op-tok left right)
  (let ((n (make-node :bin (token-pos op-tok))))
    (setf (node-s n) (token-value op-tok) (node-l n) left (node-r n) right)
    n))

(defun un-node (op-tok name operand)
  (let ((n (make-node :un (token-pos op-tok))))
    (setf (node-s n) name (node-l n) operand)
    n))

;;; --- the precedence-climbing loop -----------------------------------------

(defun parse-term (p min-bp)
  (let ((left (parse-prefix p min-bp)))
    (loop
      (let* ((tok (p-peek p))
             (entry (infix-entry tok)))
        (when (null entry) (return left))
        (let ((bp (car entry))
              (assoc (cdr entry)))
          (when (< bp min-bp) (return left))
          (p-next p)
          (cond
            ((char= assoc #\R)
             (if (member (token-value tok) +assign-ops+ :test #'string=)
                 (progn
                   (check-target left tok)
                   ;; Counted, for the same reason PARSE-PREFIX counts: the right side
                   ;; recurses through neither PARSE-SEQUENCE nor PARSE-PRIMARY, so
                   ;; uncounted a chain of assignments is bounded by nothing but this
                   ;; host's own control stack.
                   (with-depth (p (token-pos tok))
                     (let ((value (parse-term p bp))
                           (n (make-node :assign (node-pos left))))
                       (setf (node-s n) (token-value tok)
                             (node-l n) left
                             (node-r n) value)
                       (setf left n))))
                 ;; Counted like an assignment (SPEC 6.4): `??` and `???` are the
                 ;; other right-associative operators.
                 (with-depth (p (token-pos tok))
                   (setf left (bin-node tok left (parse-term p bp))))))

            ((char= assoc #\N)
             ;; Deliberately non-associative, and the E_SYNTAX is reported at the
             ;; SECOND operator rather than at the first or at the expression.
             (let* ((right (parse-term p (1+ bp)))
                    (after (p-peek p))
                    (after-entry (infix-entry after)))
               (when (and after-entry (char= (cdr after-entry) #\N))
                 (fail "E_SYNTAX"
                       (format nil "comparison operators do not chain — parenthesise, as in (a ~a b) AND (b ~a c)"
                               (token-value tok) (token-value after))
                       (token-pos after)))
               (setf left (bin-node tok left right))))

            (t
             (setf left (bin-node tok left (parse-term p (1+ bp)))))))))))

;;; NOT and unary minus.
;;;
;;; Each is accepted only where its own binding power reaches: NOT at 7 cannot
;;; appear inside a comparison operand, which is parsed at 9, so `a == NOT b`
;;; falls through to PARSE-PRIMARY -- which sees the bare identifier NOT and
;;; raises E_RESERVED. `-NOT x` is E_RESERVED for the same reason.
;;;
;;; This is the part that is not textbook. Folding prefix operators into
;;; PARSE-PRIMARY, where precedence climbing usually puts them, would make
;;; `NOT a == b` parse as `(NOT a) == b` and would break lim.parse-depth and
;;; lim.prefix-depth-does-not-shift-parens at the same time.
;;;
;;; Counted against the nesting cap (spec §6.4) like every other recursion: a
;;; prefix operator recurses through neither PARSE-SEQUENCE nor PARSE-PRIMARY,
;;; and uncounted it reached the host's own stack limit instead of E_DEPTH --
;;; a segfault in the C++ host from a rule that is just `-` repeated. Entered
;;; only when a prefix operator is actually consumed, so every other
;;; expression's trip point is unchanged.
(defun parse-prefix (p min-bp)
  (let ((tok (p-peek p)))
    (cond
      ((and (eq (token-type tok) :ident)
            (string= (token-value tok) "NOT")
            (<= min-bp +bp-not+))
       (p-next p)
       (with-depth (p (token-pos tok))
         (un-node tok "NOT" (parse-term p +bp-not+))))

      ((and (eq (token-type tok) :op)
            (string= (token-value tok) "-")
            (<= min-bp +bp-neg+))
       (p-next p)
       (with-depth (p (token-pos tok))
         (un-node tok "NEG" (parse-term p +bp-neg+))))

      (t (parse-postfix p)))))

;;; postfix = primary { "[" sequence "]" }
;;;
;;; The bracket counts a level of its own. Without it an index is the one nesting
;;; door that recurses from OUTSIDE parse-primary's counter -- this loop is where
;;; it happens -- so it charged one level per nesting where "(", "f(" and the
;;; prefix operators all charge two. Five stack frames against one level of the
;;; budget is the widest ratio in the grammar, and it put a[a[...]] over CPython's
;;; stack before the 200-level guard could fire: a host crash through the public
;;; CLI, while this host still answered. Counting the bracket halves the density
;;; and moves the boundary from about 198 nestings to 99, which is where the
;;; other hosts have been since they took the same change.
(defun parse-postfix (p)
  (let ((node (parse-primary p)))
    (loop while (or (p-at-op p "[") (p-at-op p ".>"))
          do (if (p-at-op p "[")
                 (let ((br (p-next p)))
                   (with-depth (p (token-pos br))
                     (let ((idx (parse-sequence p)))
                       (p-expect-op p "]")
                       (let ((n (make-node :index (token-pos br))))
                         (setf (node-l n) node (node-r n) idx)
                         (setf node n)))))
                 (progn
                   (p-next p)
                   (setf node (parse-pipe-step p node)))))
    node))

(defun parse-call-args (p)
  "The arguments of a call whose `(` has just been consumed, through its `)`: a
top-level `,` list is the argument list (a parenthesised one is one argument)."
  (if (p-at-op p ")")
      (progn (p-next p) '())
      (let ((inner (parse-sequence p)))
        (p-expect-op p ")")
        (if (and (eq (node-kind inner) :list) (not (node-grouped inner)))
            (node-items inner)
            (list inner)))))

(defun call-spec (name-tok)
  "The spec of the function NAME-TOK names, or E_UNKNOWN_FUNC at it."
  (or (registry-lookup-canonical (token-value name-tok))
      (fail "E_UNKNOWN_FUNC" (format nil "unknown function ~a" (token-value name-tok))
            (token-pos name-tok))))

(defun parse-pipe-step (p left)
  (let ((tok (p-peek p)))
    (when (or (not (eq (token-type tok) :ident))
              (string= (token-value tok) "TRUE")
              (string= (token-value tok) "FALSE")
              (string= (token-value tok) "NULL"))
      (fail "E_SYNTAX" "right-hand side of .> must be a function call or function name"
            (token-pos tok)))
    (let* ((name-tok (p-next p))
           (args (when (p-at-op p "(")
                   (p-next p)
                   (parse-call-args p))))
      (let ((spec (call-spec name-tok)))
        (let ((has-placeholder nil)
              (new-args (copy-list args)))
          (when (and (not (spec-binds spec)) (>= (length args) (spec-min spec)))
            (loop for sub on new-args
                  when (and (eq (node-kind (car sub)) :var)
                            (string= (node-s (car sub)) "_")
                            (not (node-grouped (car sub))))
                    do (setf (car sub) left
                             has-placeholder t)))
          (unless has-placeholder
            (setf new-args (cons left new-args)))
          (finish-call name-tok spec new-args))))))

(defun parse-primary (p)
  (let ((tok (p-peek p)))
    (with-depth (p (token-pos tok))
      (case (token-type tok)
        (:num
         (p-next p)
         ;; Canonicalised once, here: the literal 007 is the value 7.
         (let* ((text (token-value tok))
                (parsed (dec-parse text (token-pos tok)))
                (n (make-node :num (token-pos tok))))
           ;; A numeral with no leading zero in its integer part is already its own
           ;; canonical text: rendering it again only to get the same characters
           ;; is what made a million-digit literal cost seconds to compile.
           ;; (The token has no sign, so `0` and `0.5` are canonical
           ;; and `007` is not.)
           (setf (node-dec-val n) parsed
                 (node-s n) (if (or (char/= (char text 0) #\0)
                                    (= (length text) 1)
                                    (char= (char text 1) #\.))
                                text
                                (dec-format parsed)))
           n))

        (:text
         (p-next p)
         (let ((n (make-node :text (token-pos tok))))
           (setf (node-s n) (token-value tok))
           n))

        (:ident
         (cond
           ((or (string= (token-value tok) "TRUE") (string= (token-value tok) "FALSE"))
            (p-next p)
            (let ((n (make-node :bool (token-pos tok))))
              (setf (node-b n) (string= (token-value tok) "TRUE"))
              n))
           ((string= (token-value tok) "NULL")
            (p-next p)
            (make-node :null (token-pos tok)))
           (t
            (let ((after (aref (parser-toks p) (1+ (parser-i p)))))
              (if (and (eq (token-type after) :op) (string= (token-value after) "("))
                  (parse-call p)
                  (progn
                    (when (reservedp (token-value tok))
                      (fail "E_RESERVED"
                            (format nil "~a is a reserved word and cannot be a variable"
                                    (token-value tok))
                            (token-pos tok)))
                    (p-next p)
                    (let ((n (make-node :var (token-pos tok))))
                      (setf (node-s n) (token-value tok))
                      n)))))))

        (:op
         (if (string= (token-value tok) "(")
             (progn
               (p-next p)
               (when (p-at-op p ")") (fail "E_SYNTAX" "empty parentheses" (token-pos tok)))
               (let ((inner (parse-sequence p)))
                 (p-expect-op p ")")
                 ;; Marked so that F((1,2)) passes one list rather than two
                 ;; arguments. A copy, so the mark does not leak to a shared node.
                 (let ((copy (copy-node inner)))
                   (setf (node-grouped copy) t)
                   copy)))
             (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok))
                   (token-pos tok))))

        (t (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok))
                 (token-pos tok)))))))

(defun parse-call (p)
  (let ((name-tok (p-next p)))
    (p-expect-op p "(")
    (let ((args (parse-call-args p)))
      (finish-call name-tok (call-spec name-tok) args))))

;;; The target must be an identifier followed by zero or more index operations.
(defun check-target (node op-tok)
  (let ((n node))
    (loop while (eq (node-kind n) :index) do (setf n (node-l n)))
    (when (or (not (eq (node-kind n) :var)) (node-grouped node))
      (fail "E_BAD_ASSIGN"
            (format nil "cannot assign with ~a to this expression" (token-value op-tok))
            (node-pos node)))))

;;; --- binding forms ---------------------------------------------------------

;;; A host's own binding function (DEFINE-BUILTIN with :binds outside the
;;; manifest, examples/fn-complex) has no manifest forms; it gets the two
;;; classic shapes.
(defparameter *generic-binding-forms*
  '(("" (:outer :inner) nil ("_" "_K"))
    ("" (:outer :binder :inner) (1 :name) ("_K"))))

;;; The binding forms of one builtin, by name, in table order. BINDING-FORM runs for
;;; every call the dependency walker and the SQL layer visit, and filtered the whole
;;; table (a STRING= per builtin) each time. The table is data set once at
;;; load; the index is keyed on that list, so a reloaded manifest rebuilds it.
(defvar *binding-forms-index* nil)   ; (data . hash-table)

(defun binding-forms-named (name)
  "The forms of the builtin called NAME (any case), or NIL."
  (let ((cache *binding-forms-index*))
    (unless (and cache (eq (car cache) *builtin-form-data*))
      (let ((h (make-hash-table :test 'equal)))
        (dolist (f *builtin-form-data*)
          (push f (gethash (ascii-upcase (first f)) h)))
        (maphash (lambda (k v) (setf (gethash k h) (nreverse v))) h)
        (setf cache (cons *builtin-form-data* h)
              *binding-forms-index* cache)))
    (gethash (ascii-upcase name) (cdr cache))))

(defun form-when-holds-p (when args)
  "Whether the argument a manifest form's WHEN names -- (index :name) or (index
:text) -- is that: a bare, unparenthesised name, or a text literal."
  (let ((a (elt args (first when))))
    (and (node-p a)
         (ecase (second when)
           (:name (and (eq (node-kind a) :var) (not (node-grouped a))))
           (:text (eq (node-kind a) :text))))))

(defun match-binding-form (forms args)
  "The first of FORMS (manifest rows: name, scopes, when, binds) that takes
ARGS, a list or vector of argument nodes; NIL when none does. The rows' order
is the precedence: SORT_BY's text-literal-direction form comes
before its bare-binder form, so `SORT_BY(L, _, \"DESC\")` sorts by `_`, DESC."
  (let ((count (length args)))
    (dolist (form forms nil)
      (let ((scopes (second form))
            (when (third form)))
        (when (and (= (length scopes) count)
                   (or (null when) (form-when-holds-p when args)))
          (return form))))))

(defun binding-form (name args &optional (spec (registry-lookup name)))
  "Which argument of a binding call runs where (spec/builtins.md, \"Binding
forms\"): two values, the per-argument scopes -- :OUTER (evaluated where the
call is), :BINDER (a bare name, never evaluated) or :INNER (once per element)
-- and the names bound inside. NIL when the call is not a binding builtin or no
form takes this count: the evaluator would refuse it, and a static consumer
reads every argument where the call stands. The dependency walker and the SQL
layer's stage 1 both classify through here, so they cannot disagree."
  (let* ((forms (or (binding-forms-named name)
                    (and spec (spec-binds spec) *generic-binding-forms*)))
         (form (match-binding-form forms args))
         (scopes (second form)))
    (when form
      (let ((bound (copy-list (fourth form))))
        (loop for scope in scopes
              for a in args
              when (and (eq scope :binder) (node-p a) (eq (node-kind a) :var))
                do (push (node-s a) bound))
        (values scopes bound)))))

(defmacro map-call-args-by-scope ((arg scope inner) call bound &body body)
  "The list of BODY's values, one per argument ARG of the call node CALL, with
SCOPE that argument's binding scope -- :OUTER, :BINDER or :INNER, by
BINDING-FORM; every argument of a call that binds nothing is :OUTER -- and
INNER the names in scope inside the call: BOUND plus those it binds. The one
\"walk a call's arguments by binding scope\" every static walker shares; each
says in BODY what it does with a binder, an inner and an outer argument."
  (let ((scopes (gensym "SCOPES")) (binds (gensym "BINDS")) (rest (gensym "REST")))
    `(multiple-value-bind (,scopes ,binds)
         (binding-form (node-s ,call) (node-items ,call) (node-spec ,call))
       (let ((,inner (append ,binds ,bound))
             (,rest ,scopes))
         (declare (ignorable ,inner))
         (mapcar (lambda (,arg)
                   (let ((,scope (if ,scopes (pop ,rest) :outer)))
                     (declare (ignorable ,scope))
                     ,@body))
                 (node-items ,call))))))

(defun keep-binding-form (name old-args new-args pos)
  "NEW-ARGS -- a binding call's arguments after a rewrite that inlined a helper
into them -- made to select the form OLD-ARGS, the call as written, selects.
The form is read off the call as written (spec §7.3): in `D = \"DESC\";
L .> SORT_BY(r, D)` the bare name r is the binder and D the KEY, and inlining
D would turn the call into SORT_BY(r, \"DESC\"), the direction form with an
unbound key. Only SORT_BY and TOP_BY have such forms; their binder form is
pinned by spelling out its direction, ASC, at POS."
  (let* ((forms (binding-forms-named name))
         (was (second (match-binding-form forms old-args)))
         (now (second (match-binding-form forms new-args))))
    (if (or (null was) (equal was now)
            (not (member name '("SORT_BY" "TOP_BY") :test #'string=)))
        new-args
        (let ((asc (make-node :text pos)))
          (setf (node-s asc) "ASC")
          (append (subseq new-args 0 3) (list asc) (nthcdr 3 new-args))))))

(defun call-form (forms args &optional counted)
  "The roles of a binding call's arguments under the manifest form that takes
ARGS (MATCH-BINDING-FORM), as four values, each an argument index or NIL: the
binder (NIL: the binder is `_`), the first and the second argument evaluated
once per element (:INNER -- a sort's key; a bucket's key and projection), and
a direction: the first :OUTER argument after the first :INNER one, short of the
last argument when COUNTED (the TOP family, whose last argument is the count).
The one decoder of SORT/SORT_BY/TOP/TOP_BY/BUCKET forms the evaluator, the
optimiser and the translator share."
  (let ((scopes (second (match-binding-form forms args))))
    (when scopes
      (let* ((end (if counted (1- (length scopes)) (length scopes)))
             (inner1 (position :inner scopes))
             (inner2 (and inner1 (position :inner scopes :start (1+ inner1))))
             (dir (and inner1 (position :outer scopes :start (1+ inner1) :end end))))
        (values (position :binder scopes) inner1 inner2 dir)))))

