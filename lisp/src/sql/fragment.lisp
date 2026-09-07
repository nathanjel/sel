;;;; The result of a translation: a part list, its bound values, and the static
;;;; kind it produces.

(in-package #:sel.sql)

;;; EMIT.LISP is loaded after this file, because EMIT-TEXT-OPERAND needs the
;;; FRAGMENT struct at compile time while these need only its functions at run
;;; time. Declared so the forward reference is stated rather than warned about.
(declaim (ftype function emit-literal emit-placeholder lex-text replace-all))

(defparameter +kinds+ '(:num :text :bool :bin :unknown :list))

(defun kind-name (k) (string-upcase (symbol-name k)))

(defun kind-from-name (name)
  (find name +kinds+ :key #'kind-name :test #'equal))

(defstruct (fragment (:constructor %fragment (parts kind dialect
                                              &optional params param-kinds caveats)))
  "PARTS alternates finished SQL and parameter slots -- a string is SQL, an
integer is the 1-based index of a value in PARAMS. The renderer never
concatenates a literal into a string, so inline and params output are two ways
of joining ONE structure rather than two code paths. A part list cannot be
confused about where a literal ends, whatever the literal contains, and that is
the class of bug this shape exists to make unreachable."
  (parts '() :type list)
  (kind :unknown)
  (dialect "" :type string)
  (params '() :type list)
  ;; The literal form of each slot, parallel to PARAMS. Kept beside the values
  ;; rather than derived from them because it CANNOT be derived: SEL numbers ARE
  ;; text values (spec §4), so (make-num "5.00") and (make-text "5.00") are one
  ;; object.
  (param-kinds '() :type list)
  (caveats '() :type list))

(defun slot-inline-p (f slot)
  "True for a slot rendered as a literal in every mode, never as a parameter.

Three forms qualify, for the same underlying reason: NONE carries any character
the caller chose, so there is nothing for a placeholder to protect, and each is
damaged by being sent as a string.

NUM, because no coercion of a bound string reproduces a bare numeric literal:
MariaDB reads 2.50 as DECIMAL with scale 2, a parameter is untyped, and every
way of giving it a type picks the wrong one. BOOL, because the token comes out
of the dialect document rather than out of a rule -- bound as a string it breaks
SQLite outright, where 1 = '1' is 0. BIN, because a BIN parameter is bytes and a
driver sends them through the connection's text encoding: on PostgreSQL 130 of
the 256 single-byte values then failed."
  (let ((k (nth (1- slot) (fragment-param-kinds f))))
    (and k (member k '(:num :bool :bin)) t)))

(defun frag-join (f mode)
  (let ((nth 0))
    (with-output-to-string (out)
      (dolist (p (fragment-parts f))
        (if (stringp p)
            (write-string p out)
            (let* ((i (1- p))
                   ;; A slot whose kind was never recorded is quoted rather than
                   ;; emitted bare, matching the other hosts.
                   (form (or (nth i (fragment-param-kinds f)) :text))
                   (v (nth i (fragment-params f))))
              (if (and (not (eq mode :inline)) (slot-inline-p f p))
                  ;; Never a placeholder, and it does not advance NTH either,
                  ;; because it emits no placeholder for a binding to land in.
                  (write-string (emit-literal (fragment-dialect f) v form) out)
                  (progn
                    (incf nth)
                    (ecase mode
                      (:inline (write-string (emit-literal (fragment-dialect f) v form) out))
                      ;; The ordinal a numbered placeholder carries -- PostgreSQL's
                      ;; $n -- must agree with BINDINGS, which walks the output. The
                      ;; slot id would not: it is a CREATION number, and a
                      ;; reordering template emits creation numbers out of order.
                      (:params (write-string (emit-placeholder (fragment-dialect f) nth) out))
                      ;; ~D, never ~a: ~a renders through *PRINT-BASE*, which
                      ;; belongs to the calling application. A slot ordinal is a
                      ;; position in the output, not a number for the caller to
                      ;; format, and under a rebound base slot 10 came out ~A~.
                      (:debug (format out "~~~D~~" nth)))))))))))

(defun as-value (f &optional (mode :inline))
  "Usable in a select list, GROUP BY, ORDER BY or HAVING. Any kind but LIST,
which is not a SQL value at all."
  (when (eq (fragment-kind f) :list)
    (refuse "E_SQL_SHAPE"
            "this expression yields a list, and a SQL expression is a scalar"))
  (frag-join f mode))

(defun as-condition (f &optional (mode :inline))
  "Usable as a condition.

BOOL as it stands; UNKNOWN wrapped in the dialect's IS TRUE test, since a column
of unknown type may be NULL and SEL has no third truth value to give back. A NUM
or TEXT fragment is refused rather than accepted: silently allowing
`WHERE o.total` is how a database turns a validation rule into the truthiness
test SEL spent its whole design avoiding."
  (case (fragment-kind f)
    (:bool (frag-join f mode))
    (:unknown (replace-all (lex-text (fragment-dialect f) "isTrue") "{0}"
                           (frag-join f mode)))
    (t (refuse "E_SQL_SHAPE"
               (format nil "a condition must be BOOL, and this expression is ~a; ~
SQL has no truthiness and neither does SEL" (kind-name (fragment-kind f)))))))

(defun bindings (f)
  "The bound values for :PARAMS mode, in PLACEHOLDER order.

Derived from the part list rather than returned as stored, because the two
orders are not the same. A slot is numbered when it is created, and the template
decides where it lands: FIND(needle, hay) maps to INSTR({1}, {0}), so the second
slot created is the first one emitted. A slot appearing more than once yields
its value more than once, which is also right."
  (let ((out '()))
    (dolist (p (fragment-parts f) (nreverse out))
      (when (and (integerp p) (not (slot-inline-p f p)))
        (push (nth (1- p) (fragment-params f)) out)))))
