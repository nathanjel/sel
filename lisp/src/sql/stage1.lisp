;;;; Stage 1: does SEL itself accept this expression, and can the program be
;;;; reduced to ONE expression.
;;;;
;;;; **constants** answers a question the translator was not asking. Its job is
;;;; "can this rule be pushed into that database", and it used to answer that
;;;; without ever asking whether the rule is VALID. LEFT("abc", -1) translates
;;;; cleanly into every dialect, and SEL raises E_RANGE for it while MariaDB
;;;; answers '', PostgreSQL 'ab' and SQLite 'abc'. Three databases, three
;;;; answers, none of them SEL's -- from a translation that reported success.
;;;;
;;;; It is validation, not constant folding. The value is computed and thrown
;;;; away; the translation that follows is byte-identical to the one that would
;;;; have been emitted without the check. Folding would have blinded the oracle.
;;;;
;;;; **normalise** is the restrictive step. SEL is an expression language, but it
;;;; has assignment and `;`, and SQL has neither.

(in-package #:sel.sql)

(defstruct (clist (:constructor make-clist (pos)))
  "A keyed list, built by indexed assignment and by nothing else.

Distinct from a `list` node because it must NOT be flattened: assignment stores
a list as a child rather than contributing its children (spec §5.9 applies to
`,` and not to `=`), so `R[1] = (1, 2); R[2] = (3, 4)` is two pairs and not four
scalars. It carries the real keys too, so `R[\"a\"] = 1` gives `_K` of \"a\".

MUTABLE and shared, because the aliasing is observable: `R[1] = 1; A = R;
R[2] = 2; COUNT(A)` is 2, since A holds the same list R does. Copying on append
would answer 1.

POS is the FIRST indexed assignment's and survives every later append.

A struct of its own rather than a field on SEL's node: the parser never produces
one and the evaluator has never seen one. It lives in a node's child slots
because those are untyped -- which is what lets this host skip the wrapper type
the C++ port needed."
  (pos nil)
  (entries '() :type list))

(defun snode-kind (n) (if (clist-p n) :clist (sel::node-kind n)))
(defun snode-pos (n) (if (clist-p n) (clist-pos n) (sel::node-pos n)))

(defun is-binder-name (n)
  "A binder ARGUMENT, not a read of one: ALL(V, C, C > 0) names C in argument 1.
Grouping is what tells them apart -- ALL(V, (C), C > 0) passes the VALUE of C,
which SEL refuses with E_EXPECT_SYMBOL, and which used to translate to working
SQL for a rule that can never run."
  (and (not (clist-p n))
       (eq (sel::node-kind n) :var)
       (not (sel::node-grouped n))))

;;; --- constants ------------------------------------------------------------

(defun const-scope (bs)
  "The scalar `value` bindings, as a name set and an evaluation root.

A value binding is a constant the translator HAS -- §5.4 calls it \"a constant
supplied at translation time, inlined as a literal\" -- so LEFT(\"abc\", X) with
X bound to \"-1\" is exactly as knowable as LEFT(\"abc\", -1), and before this it
was exactly as wrong.

Only scalars are lifted. A list-valued binding is what an aggregate iterates and
its shape is the translator's business, not the evaluator's."
  (let ((names '()) (root (sel:make-none)))
    (when bs
      (dolist (name (bindings-names bs))
        (let ((b (bindings-get bs name)))
          (when (eq (binding-kind b) :value)
            (let ((v (getf (binding-spec b) :value)))
              (when (and (not (sel:value-none-p v)) (zerop (sel:value-size v)))
                (push name names)
                (sel:value-set root name v)))))))
    (values names root)))

(defun constant-call-p (n bound)
  "The binding form is the only reason this is not three lines. MAP(list, X,
X + 1) names its binder in argument 1 and uses it in argument 2; the two-argument
form binds `_` implicitly. Neither name is a free variable, so neither
disqualifies the call -- but the SOURCE still has to be constant, or the body has
nothing to iterate."
  (let ((args (sel::node-items n))
        (spec (sel::node-spec n)))
    (if (not (and spec (sel::spec-binds spec)))
        (every (lambda (a) (is-constant a bound)) args)
        (and args
             (is-constant (first args) bound)
             (let ((inner (copy-list bound))
                   (body 1))
               (if (>= (length args) 3)
                   (progn
                     ;; Malformed; not constant, and the aggregate refuses it for
                     ;; real.
                     (unless (is-binder-name (second args)) (return-from constant-call-p nil))
                     (push (sel::node-s (second args)) inner)
                     (setf body 2))
                   (push "_" inner))
               (loop for i from body below (length args)
                     always (is-constant (nth i args) inner)))))))

(defun is-constant (n bound)
  "Whether every leaf under N is a literal. A binder an aggregate introduces
inside N counts as bound, so ALL((1, 2), _ > 0) is constant and
ALL(ITEMS, _ > 0) is not -- the same rule the evaluator applies, which is what
lets the whole node be handed to it."
  (case (snode-kind n)
    ((:num :text :bool) t)
    (:var (and (member (sel::node-s n) bound :test #'equal) t))
    (:un (is-constant (sel::node-l n) bound))
    ((:bin :index) (and (is-constant (sel::node-l n) bound)
                        (is-constant (sel::node-r n) bound)))
    ;; Stage 1 builds this one; the evaluator has never seen it and cannot
    ;; evaluate it. Nothing containing one is checkable.
    (:clist nil)
    (:list (every (lambda (i) (is-constant i bound)) (sel::node-items n)))
    (:call (constant-call-p n bound))
    ;; assign and seq are gone by now, and an unknown node is not something to
    ;; guess about: not constant, so nothing is validated and the walk refuses it
    ;; in the ordinary way.
    (t nil)))

(defun validate-constant (n root)
  "Evaluate N the way SEL would, and refuse the translation if SEL refuses.

Called AFTER the node has been translated, so every refusal the translator
already had keeps its own message: `TRUE + 1` is both an expression SEL rejects
and a BOOL where a number is required, and the second is the sentence an author
can act on. This check adds refusals where translation used to SUCCEED and
changes none that already existed.

The position reported is SEL's own -- the innermost node that failed, not the
outermost one this was called with -- because that is the character the author
has to change."
  (handler-case
      ;; SEL:RUN on a program built from this node IS what the Python host
      ;; reaches Context and eval-node for. No evaluator internals are needed.
      (sel:run (sel::%make-program "" n) root)
    (sel:sel-error (e)
      (refuse "E_SQL_INVALID"
              (format nil "SEL rejects this expression (~a: ~a), so there is ~
nothing to translate; a database would answer something rather than fail"
                      (sel:sel-error-code e) (sel:sel-error-message e))
              (if (plusp (sel:sel-error-line e))
                  (sel::make-pos (sel:sel-error-line e) (sel:sel-error-col e)
                                 (sel:sel-error-offset e))
                  (snode-pos n)))))
  (values))

;;; --- normalise ------------------------------------------------------------

(defun constant-key (idx)
  "The literal key an index expression names, or NIL when it is not one.

`num` and `text` ONLY. A BOOL index is refused -- A[TRUE] = 1 is E_SQL_ASSIGN --
so spelling this as \"is a literal node\" would silently accept a key this layer
does not.

The key is the parser's own characters. It normalises leading zeros and nothing
else, so R[1.0] and R[1] are two different keys and SEL agrees:
`R[1.0] = 1; R[1] = 2; JOIN(MAP(R, _K), \"|\")` is \"1.0|1\". Canonicalising here
would collapse them, or turn a legal program into a duplicate-key refusal."
  (when (member (snode-kind idx) '(:num :text)) (sel::node-s idx)))

(defun replace-items (n items)
  (let ((c (sel::copy-node n))) (setf (sel::node-items c) items) c))

(defun substitute-node (node defs bound depth)
  "Replace every read of a defined name with the node it was assigned.

The substituted subtree keeps its original POS, so an error inside an inlined
expression still points at where the author wrote it rather than at the place it
was used. Every branch that changes a child COPIES the node first: a node is a
mutable struct shared with the caller's AST, and rewriting one in place would
leave the program permanently substituted -- visible to the next translation of
the same program, and to the evaluator.

Bounded at the EVALUATOR's own limit, because stage 1 walks the tree before the
translator's guard can reach it. Without this the deepest expression the layer
accepts was decided by the host."
  (incf depth)
  (when (> depth sel::+max-depth+)
    (refuse "E_SQL_DEPTH"
            (format nil "this expression nests deeper than SEL will evaluate ~
(~a), so there is nothing to translate; the evaluator answers E_DEPTH for it"
                    sel::+max-depth+)
            (snode-pos node)))
  (case (snode-kind node)
    (:var
     ;; BOUND before DEFS: an aggregate binder SHADOWS a same-named helper.
     ;; `B = 7; ALL((1,2), B, B > 0)` translates to (1 > 0) AND (2 > 0) -- the
     ;; helper is never inlined into the body.
     (if (member (sel::node-s node) bound :test #'equal)
         node
         (let ((cell (assoc (sel::node-s node) defs :test #'equal)))
           (if cell (cdr cell) node))))
    ((:num :text :bool) node)
    (:assign (refuse "E_SQL_ASSIGN"
                     "an assignment here would have to happen while the query ~
runs, and a SQL expression cannot assign" (snode-pos node)))
    (:seq (refuse "E_SQL_ASSIGN"
                  "a sequence here would evaluate and discard a value, which a ~
SQL expression cannot do" (snode-pos node)))
    (:un (let ((c (sel::copy-node node)))
           (setf (sel::node-l c) (substitute-node (sel::node-l node) defs bound depth))
           c))
    ((:bin :index)
     (let ((c (sel::copy-node node)))
       (setf (sel::node-l c) (substitute-node (sel::node-l node) defs bound depth)
             (sel::node-r c) (substitute-node (sel::node-r node) defs bound depth))
       c))
    (:list (replace-items node (flatten-items (sel::node-items node) defs bound depth)))
    (:clist (let ((out (make-clist (clist-pos node))))
              (setf (clist-entries out)
                    (mapcar (lambda (cell)
                              (cons (car cell)
                                    (substitute-node (cdr cell) defs bound depth)))
                            (clist-entries node)))
              out))
    (:call
     (let* ((args (sel::node-items node))
            (spec (sel::node-spec node))
            (binds (and spec (sel::spec-binds spec)))
            (inner (copy-list bound)))
       (when binds
         (push "_K" inner)
         (push (if (and (= (length args) 3) (is-binder-name (second args)))
                   (sel::node-s (second args))
                   "_")
               inner))
       (replace-items
        node
        (loop for arg in args
              for i from 0
              collect (if (and binds (= i 1) (= (length args) 3)
                               (eq (snode-kind arg) :var))
                          ;; An aggregate's binder argument is a NAME, not a read
                          ;; of one.
                          arg
                          (substitute-node arg defs (if (= i 0) bound inner) depth))))))
    (t node)))

(defun flatten-items (items defs bound depth)
  "Build a `,` list, flattening per spec §5.9: an operand with children and no
scalar of its own contributes each of its children, and the keys are renumbered
from 1.

Both node kinds contribute, and both contribute exactly ONE level -- per
application, not net: a nested list literal is already flat by the time this sees
it, because SUBSTITUTE-NODE recursed into the inner list first. Only a clist's
values stop the recursion, which is the whole reason clist exists."
  (let ((out '()))
    (dolist (item items (nreverse out))
      (let ((s (substitute-node item defs bound depth)))
        (case (snode-kind s)
          (:list (dolist (i (sel::node-items s)) (push i out)))
          (:clist (dolist (cell (clist-entries s)) (push (cdr cell) out)))
          (t (push s out)))))))

(defun record-statement (s defs const-names root)
  "Fold one leading statement into DEFS, or refuse it. Every refusal reports the
ASSIGN's position, which is its target's position -- not the `=` and not the
statement start.

Returns the possibly-extended DEFS."
  (unless (eq (snode-kind s) :assign)
    (refuse "E_SQL_ASSIGN"
            "only assignments may come before the result expression; this ~
computes a value nothing reads, which SQL has nowhere to put" (snode-pos s)))
  (unless (equal (sel::node-s s) "=")
    (refuse "E_SQL_ASSIGN"
            (format nil "~a reads its own target before writing it, and SQL has ~
nowhere to put the write; use = and a fresh name" (sel::node-s s))
            (snode-pos s)))
  ;; The target is a bare name, or a name indexed by constant keys. The walk is
  ;; OUTSIDE-IN, so with two non-constant indices the outermost is refused
  ;; first: A[X][Y] = 1 reports the Y bracket.
  (let ((keys '()) (target (sel::node-l s)))
    (loop while (eq (snode-kind target) :index)
          do (let ((k (constant-key (sel::node-r target))))
               (unless k
                 (refuse "E_SQL_ASSIGN"
                         "an assignment target may only be indexed by a constant ~
here, because the shape has to be known before the query runs"
                         (snode-pos (sel::node-r target))))
               (push k keys)
               (setf target (sel::node-l target))))
    (unless (eq (snode-kind target) :var)
      (refuse "E_SQL_ASSIGN" "assignment target is not a variable" (snode-pos s)))
    (let ((name (sel::node-s target))
          (value (substitute-node (sel::node-r s) defs '() 0)))
      ;; Validated here, and ONLY here, because after this the subtree may be
      ;; gone: a definition nothing reads is dropped, so `A = 1 / 0; TRUE`
      ;; translated to TRUE and every server answered TRUE where SEL raises
      ;; E_DIV_ZERO. An indexed assignment builds a clist, which the constant
      ;; test refuses to walk, so `R[1] = 1 / 0; COUNT(R)` was 1.
      (when (is-constant value const-names) (validate-constant value root))
      (cond
        ((null keys)
         (when (assoc name defs :test #'equal)
           (refuse "E_SQL_ASSIGN"
                   (format nil "~a is assigned more than once; SQL has no notion ~
of a variable changing, so each name may be written once" name)
                   (snode-pos s)))
         (append defs (list (cons name value))))
        ((> (length keys) 1)
         (refuse "E_SQL_ASSIGN"
                 "only one level of indexed assignment can be folded into a list ~
here" (snode-pos s)))
        (t
         (let* ((key (first keys))
                (cell (assoc name defs :test #'equal))
                (cl (if cell (cdr cell) (make-clist (snode-pos s)))))
           (unless (clist-p cl)
             (refuse "E_SQL_ASSIGN"
                     (format nil "~a is assigned both as a whole and by index; ~
use one or the other" name)
                     (snode-pos s)))
           (when (assoc key (clist-entries cl) :test #'equal)
             (refuse "E_SQL_ASSIGN"
                     (format nil "~a[~a] is assigned more than once" name key)
                     (snode-pos s)))
           ;; Appended in place, so a reference an earlier statement already took
           ;; sees it. That aliasing is observable.
           (setf (clist-entries cl)
                 (append (clist-entries cl) (list (cons key value))))
           (if cell defs (append defs (list (cons name cl))))))))))

(defun normalise (ast const-names root)
  "Turn a program into one expression, or refuse it.

Can return a bare clist -- `R[1] = 1; R` -- which the translator then refuses as
a shape. The result is not always an expression node."
  (let* ((stmts (if (eq (sel::node-kind ast) :seq) (copy-list (sel::node-items ast)) (list ast)))
         ;; The LAST statement is the result, whatever it is. A trailing
         ;; assignment is not refused here: it is popped as the result and
         ;; refused by SUBSTITUTE-NODE's assign branch, which is why `A = 1`
         ;; reports at 1:1.
         (result (car (last stmts)))
         (leading (butlast stmts))
         (defs '()))
    (dolist (s leading)
      (setf defs (record-statement s defs const-names root)))
    (substitute-node result defs '() 0)))
