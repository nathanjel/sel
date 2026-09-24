;;;; Public host interface. See spec/SPEC.md §8.

(in-package #:sel)

(defstruct (program (:constructor %make-program (source ast)))
  (source "" :type string)
  ;; The parse tree, and the tree every other consumer reads: DEPENDENCIES, the
  ;; SQL translator, the hybrid planner. It is IMMUTABLE once here -- the
  ;; optimiser and the planner copy on the way down and never write into it
  ;; (sql/cases/25-hybrid-plans.sqlt asserts so) -- and a caller who builds a
  ;; program from an AST of their own is held to the same rule. Setting the
  ;; slot to a new tree is fine and drops the cache below; writing into its
  ;; nodes is not. Private argument-vector metadata may be filled lazily; it
  ;; never changes the tree's structure or stores invocation-specific values.
  (ast nil)
  ;; The physical tree RUN evaluates: AST after the in-memory optimiser, built
  ;; on the first run and kept, because the rewrite and the copy it makes cost
  ;; more than evaluating a small rule does. Keyed by the identity of AST so a
  ;; new tree is noticed. Private; SQL translation never sees it, since a
  ;; physical rewrite (join pushdown) is not something a database
  ;; can be asked to run. Two threads racing to fill it compute the same
  ;; immutable tree and one wins; that is benign.
  (%physical nil)
  (%physical-of nil))

(defun program-physical-ast (program)
  "The optimised tree RUN evaluates, built once per AST."
  (let ((ast (program-ast program)))
    (unless (eq (program-%physical-of program) ast)
      (setf (program-%physical program) (optimize-ast ast)
            (program-%physical-of program) ast))
    (program-%physical program)))

(defun compile-source (source)
  "Compile SOURCE, raising SEL-ERROR on any compile-time failure: syntax, an
unknown function, a wrong argument count, a non-portable regex literal."
  (%make-program source (parse-source source)))

(defun run (program &optional context)
  "Evaluate PROGRAM against CONTEXT, whose direct children are the variables.
CONTEXT may be a value, CL data FROM-NATIVE understands, or omitted. The context
is mutated in place by any assignment the program performs."
  (let ((root (cond ((null context) (make-none))
                    ((value-p context) context)
                    (t (from-native context)))))
    (eval-node (program-physical-ast program) (make-context root))))

(defun evaluate (source &optional context)
  (run (compile-source source) context))

;;; --- dependencies ----------------------------------------------------------

(defun collect-deps (node bound reads assigned depth)
  "BOUND, READS and ASSIGNED are hash tables keyed by name.

The static walk of the tree, and the third thing in each host that recurses
over it. spec/SPEC.md 6.4 caps the other two -- the parser's nesting and the
evaluator's -- and says why: uncounted recursion over a tree the source can
make arbitrarily deep reaches the host's own stack limit. This walk was
uncounted, and `dependencies()` on a flat chain of about 48,000 operators
exhausted the control stack.

The depth rides as a parameter rather than as a counter with a guard, because
there is nothing to release on the way out -- which is also what lets the five
hosts spell this identically. Capped at the same MAX_DEPTH the evaluator uses
and tripping at the same node, so a program whose dependencies cannot be
computed is exactly a program that could not have been evaluated."
  (when node
    (when (> depth +max-depth+)
      (fail "E_DEPTH" "expression nested too deeply" (node-pos node)))
    (case (node-kind node)
      (:var
       (unless (gethash (node-s node) bound)
         (setf (gethash (node-s node) reads) t)))

      (:assign
       (let ((target (node-l node))
             (n (node-l node)))
         (loop while (eq (node-kind n) :index)
               do (collect-deps (node-r n) bound reads assigned (1+ depth))
                  (setf n (node-l n)))
         ;; `A = x` defines A; `A[k] = x` and `A += x` also read it.
         (when (or (not (eq (node-kind target) :var))
                   (not (string= (node-s node) "=")))
           (unless (gethash (node-s n) bound)
             (setf (gethash (node-s n) reads) t)))
         (setf (gethash (node-s n) assigned) t)
         (collect-deps (node-r node) bound reads assigned (1+ depth))))

      (:call
       ;; Which arguments run inside the binder, and what they see, is decided
       ;; once, by BINDING-FORM over the manifest's forms (spec/builtins.md).
       ;; No form -- a strict function, or a count the evaluator would refuse
       ;; -- and every argument is read where the call stands.
       (let ((items (node-items node)))
         (multiple-value-bind (scopes binds) (binding-form (node-s node) items (node-spec node))
           (if (null scopes)
               (dolist (arg items) (collect-deps arg bound reads assigned (1+ depth)))
               (let ((inner nil))
                 (loop for scope in scopes
                       for arg in items
                       do (case scope
                            (:binder nil)
                            (:inner
                             (unless inner
                               (setf inner (copy-name-table bound))
                               (dolist (b binds) (setf (gethash b inner) t)))
                             (collect-deps arg inner reads assigned (1+ depth)))
                            (t (collect-deps arg bound reads assigned (1+ depth))))))))))

      ((:seq :list)
       (dolist (item (node-items node)) (collect-deps item bound reads assigned (1+ depth))))

      ((:index :bin)
       (collect-deps (node-l node) bound reads assigned (1+ depth))
       (collect-deps (node-r node) bound reads assigned (1+ depth)))

      (:un (collect-deps (node-l node) bound reads assigned (1+ depth)))

      (t nil))))

(defun copy-name-table (table)
  (let ((out (make-hash-table :test #'equal)))
    (maphash (lambda (k v) (setf (gethash k out) v)) table)
    out))

(defun dependencies (program)
  "Every variable PROGRAM reads without having assigned it first, found
statically and returned sorted in upper case. Possible only because SEL has no
dynamic symbol operator; this is how a frontend knows which inputs should
re-trigger which rule."
  (let ((bound (make-hash-table :test #'equal))
        (reads (make-hash-table :test #'equal))
        (assigned (make-hash-table :test #'equal)))
    (collect-deps (program-ast program) bound reads assigned 1)
    (sort (loop for name being the hash-keys of reads
                unless (gethash name assigned) collect name)
          #'string<)))

;;; Every shipped builtin is loaded by now; the manifest must not name one more.
(assert-builtin-manifest-covered)

;;; --- host functions (spec/SPEC.md §8.1) --------------------------------------

;;; Names registered through REGISTER-FUNCTION, which alone may be replaced.
(defvar *host-functions* (make-hash-table :test #'equal))

(defun host-function-name-p (name)
  (and (stringp name)
       (plusp (length name))
       (let ((c (char name 0))) (or (char<= #\A c #\Z) (char<= #\a c #\z)))
       (every (lambda (c) (or (char<= #\A c #\Z) (char<= #\a c #\z) (char<= #\0 c #\9) (char= c #\_)))
              name)))

(defun register-function (name min max fn)
  "Add an application's own strict function, callable from programs compiled
afterwards. FN receives the argument accessor (ARGS-COUNT, ARGS-VAL, ARGS-TEXT,
ARGS-BOOL, ARGS-INT, ARGS-NON-NEG-INT, ARGS-POS-OF) and returns a new VALUE. A
host function adds to the language and never changes it: a malformed or
reserved name, a builtin's name or an arity outside 0 <= MIN <= MAX signals a
plain ERROR, not a SEL-ERROR. Registering a host function's name again replaces
it."
  (unless (host-function-name-p name)
    (error "SEL function name must be ASCII letters, digits and _, starting with a letter: ~s" name))
  (let ((upper (string-upcase name)))
    (when (reservedp upper)
      (error "~a is a reserved word" upper))
    (when (and (gethash upper *registry*) (not (gethash upper *host-functions*)))
      (error "~a is a builtin; a host function cannot replace it" upper))
    (unless (and (integerp min) (integerp max) (<= 0 min max))
      (error "SEL function ~a: arity must be whole numbers with 0 <= min <= max" upper))
    (unless (functionp fn)
      (error "SEL function ~a: fn is not a function" upper))
    (setf (gethash upper *registry*)
          (make-spec upper min max nil nil nil
                     (lambda (a ctx)
                       (declare (ignore ctx))
                       (let ((result (funcall fn a)))
                         (unless (value-p result)
                           (error "SEL function ~a returned ~s, not a VALUE" upper result))
                         result))))
    (setf (gethash upper *host-functions*) t)
    upper))

(defun host-arity (name)
  "The (MIN . MAX) of a host function registered with REGISTER-FUNCTION, or NIL
when the name is not one. The SQL layer reads it: a host function's SQL spelling
is checked against, and recorded with, this arity."
  (let ((key (ascii-upcase name)))
    (when (gethash key *host-functions*)
      (let ((spec (gethash key *registry*)))
        (cons (spec-min spec) (spec-max spec))))))
