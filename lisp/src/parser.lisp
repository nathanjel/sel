;;;; Precedence climbing. The sixteen levels of spec/SPEC.md §5 are the table
;;;; below rather than sixteen functions, so adding an operator is adding a row.
;;;; python/sel/parser.py is the reference implementation of this shape and its
;;;; module docstring is the rationale; docs/EXTENDING.md, "Adding an operator",
;;;; step 5, records what every host had to get right, each item of which
;;;; produces a valid parse of the WRONG TREE when it is wrong.
;;;;
;;;; `;` and `,` stay hand-written N-ary loops outside the table, because they
;;;; build N-ary nodes rather than binary ones -- DEPENDENCIES walks `items`, and
;;;; PARSE-CALL flattens a top-level list into the argument vector.

(in-package #:sel)


(defstruct (node (:constructor make-node (kind pos)))
  ;; :num :text :bool :var :index :seq :list :un :bin :assign :call
  (kind :num :type keyword)
  (pos nil)
  (s "" :type string)      ; num/text literal, var name, or operator
  (b nil)                  ; :bool value
  (grouped nil)            ; came from ( ), so F((1,2)) passes one list not two args
  (l nil)                  ; bin/index/assign left; un operand
  (r nil)                  ; bin/index/assign right
  (items nil :type list)   ; seq/list items, call arguments
  (spec nil))              ; call

;;; The operator families, named once for the PARSER. The precedence table below
;;; is BUILT from these rather than repeating them, and EVAL-BINARY asks
;;; +compare-ops+ whether an operator is a numeric comparison -- so the parser and
;;; the evaluator cannot disagree about what a comparison is.
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
(defconstant +bp-bor+ 9)
(defconstant +bp-bxor+ 10)
(defconstant +bp-band+ 11)
(defconstant +bp-concat+ 12)   ; &
(defconstant +bp-add+ 13)      ; + -
(defconstant +bp-mul+ 14)      ; * / %
(defconstant +bp-neg+ 15)      ; prefix

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
    (setf (gethash "&" m) (cons +bp-concat+ #\L)
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

(defun arity-text (spec)
  (let ((plural (if (= (spec-min spec) 1) "" "s")))
    (cond ((>= (spec-max spec) +variadic+)
           (format nil "at least ~d argument~a" (spec-min spec) plural))
          ((= (spec-min spec) (spec-max spec))
           (format nil "~d argument~a" (spec-min spec) plural))
          (t (format nil "~d to ~d arguments" (spec-min spec) (spec-max spec))))))

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
             ;; Assignment. The target is validated against the AST shape, not
             ;; against a value, which is what makes `(A) = 1` a compile error.
             ;; The right side is parsed at BP rather than BP + 1, which is what
             ;; makes it right-associative.
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
;;; raises E_RESERVED, the same error the transcribed parser gave, by a different
;;; route. `-NOT x` is E_RESERVED for the same reason.
;;;
;;; This is the part that is not textbook. Folding prefix operators into
;;; PARSE-PRIMARY, where precedence climbing usually puts them, would make
;;; `NOT a == b` parse as `(NOT a) == b` and would break lim.parse-depth and
;;; lim.prefix-depth-does-not-shift-parens at the same time.
;;;
;;; Counted, for the reason the two functions this replaced were counted: a
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
;;; other four hosts have been since they took the same change.
(defun parse-postfix (p)
  (let ((node (parse-primary p)))
    (loop while (p-at-op p "[")
          do (let ((br (p-next p)))
               (with-depth (p (token-pos br))
                 (let ((idx (parse-sequence p)))
                   (p-expect-op p "]")
                   (let ((n (make-node :index (token-pos br))))
                     (setf (node-l n) node (node-r n) idx)
                     (setf node n))))))
    node))

(defun parse-primary (p)
  (let ((tok (p-peek p)))
    (with-depth (p (token-pos tok))
      (case (token-type tok)
        (:num
         (p-next p)
         ;; Canonicalised once, here: the literal 007 is the value 7.
         (let ((n (make-node :num (token-pos tok))))
           (setf (node-s n) (dec-format (dec-parse (token-value tok) (token-pos tok))))
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
    (let ((args '()))
      (if (p-at-op p ")")
          (p-next p)
          (let ((inner (parse-sequence p)))
            (p-expect-op p ")")
            (setf args (if (and (eq (node-kind inner) :list) (not (node-grouped inner)))
                           (node-items inner)
                           (list inner)))))
      (let ((spec (registry-lookup (token-value name-tok))))
        (unless spec
          (fail "E_UNKNOWN_FUNC" (format nil "unknown function ~a" (token-value name-tok))
                (token-pos name-tok)))
        (let ((count (length args)))
          (when (or (< count (spec-min spec)) (> count (spec-max spec)))
            (fail "E_ARITY"
                  (format nil "~a takes ~a, got ~d" (spec-name spec) (arity-text spec) count)
                  (token-pos name-tok)))
          (when (spec-arity-error spec)
            (let ((problem (funcall (spec-arity-error spec) count)))
              (when problem (fail "E_ARITY" problem (token-pos name-tok)))))
          (let ((n (make-node :call (token-pos name-tok))))
            (setf (node-s n) (spec-name spec)
                  (node-spec n) spec
                  (node-items n) args)
            n))))))

;;; The target must be an identifier followed by zero or more index operations.
(defun check-target (node op-tok)
  (let ((n node))
    (loop while (eq (node-kind n) :index) do (setf n (node-l n)))
    (when (or (not (eq (node-kind n) :var)) (node-grouped node))
      (fail "E_BAD_ASSIGN"
            (format nil "cannot assign with ~a to this expression" (token-value op-tok))
            (node-pos node)))))
