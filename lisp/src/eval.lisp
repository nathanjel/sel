;;;; The evaluator, and the argument framework built-ins are written against.
;;;;
;;;; Nothing here catches a sel-error. An error surfaces from the innermost node
;;;; that failed, carrying that node's position, and no layer rewrites it.

(in-package #:sel)

(defstruct (context (:constructor make-context (root)))
  (root nil)
  ;; Aggregate binders. The only scoping SEL has: one name for the duration of
  ;; one element, pushed by the aggregates and popped again afterwards.
  (frames nil :type list)
  (depth 0 :type fixnum)
  ;; A FILTER over a LINK hands the join its conjuncts here, a JOIN-PREFILTER;
  ;; the join reports back which ones every row it emitted has passed, whether
  ;; it kept a row on an error and whether it dropped any, a JOIN-REPORT
  ;; (builtins/structure.lisp, DO-LINK; SEL-0052, SEL-0054).
  (join-prefilter nil)
  (join-prefilter-report nil))

;;; The arithmetic operators on decimals, by BINARY-OP-CODE's keyword: one
;;; dispatch for EVAL-BINARY, compound assignment and the optimiser's constant
;;; fold. The math-plan executor keeps an arm per operator instead -- it runs
;;; once per step of a numeric loop, where a second dispatch is measurable.
(declaim (inline dec-arith))
(defun dec-arith (code a b pos)
  (ecase code
    (:add (dec-add a b pos))
    (:sub (dec-sub a b pos))
    (:mul (dec-mul a b pos))
    (:div (dec-div a b pos))
    (:mod (dec-mod a b pos))))

(declaim (inline ctx-lookup ctx-bound-p))
(defun ctx-lookup (ctx name)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type context ctx) (type string name))
  (dolist (frame (context-frames ctx))
    (declare (type list frame))
    (dolist (cell frame)
      (declare (type cons cell))
      (let ((k (car cell)))
        (when (and (stringp k) (or (eq k name) (string= (the string k) name)))
          (return-from ctx-lookup (cdr cell))))))
  (value-get (context-root ctx) name))

(defun ctx-bound-p (ctx name)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type context ctx) (type string name))
  (dolist (frame (context-frames ctx) nil)
    (declare (type list frame))
    (dolist (cell frame)
      (declare (type cons cell))
      (let ((k (car cell)))
        (when (and (stringp k) (or (eq k name) (string= (the string k) name)))
          (return-from ctx-bound-p t))))))

(defun ctx-push-frame (ctx frame) (push frame (context-frames ctx)))
(defun ctx-pop-frame (ctx) (pop (context-frames ctx)))

;;; --- arguments -------------------------------------------------------------

;;; Wraps the flattened argument vector. Values are evaluated at most once, so a
;;; built-in body can read the same argument repeatedly without thinking about
;;; it, and typed accessors report failures against the argument's own position.
(defstruct (args (:constructor %make-args (nodes name pos ctx cache)))
  (nodes #() :type simple-vector)
  (name "" :type string)
  (pos nil)
  (ctx nil)
  (cache #() :type vector)
  (record-shape nil))

(defun make-args (node ctx)
  (let* ((items (node-items node))
         (plan (node-argument-plan node))
         (nodes (if (and plan (eq (car plan) items))
                    (cdr plan)
                    (let ((prepared (coerce items 'simple-vector)))
                      (setf (node-argument-plan node) (cons items prepared))
                      prepared))))
    (let ((a (%make-args nodes (node-s node) (node-pos node) ctx
                         (make-array (length nodes) :initial-element :unset))))
      (setf (args-record-shape a) (node-record-shape node))
      a)))

(defun args-count (a) (length (args-nodes a)))

(defun args-refuse-index (a i)
  "A registered function asked for an argument its call does not have (spec/SPEC.md
8.1): E_BAD_ARG at the call, never a host condition."
  (fail "E_BAD_ARG"
        (format nil "~a has ~d argument~:p, and argument ~d was asked for"
                (args-name a) (args-count a) i)
        (args-pos a)))

(declaim (inline args-node))
(defun args-node (a i)
  (let ((nodes (args-nodes a)))
    (if (and (typep i 'fixnum) (<= 0 i) (< i (length nodes)))
        (svref nodes i)
        (args-refuse-index a i))))

(defun args-pos-of (a i) (node-pos (args-node a i)))

(defun args-val (a i)
  (args-node a i)                       ; the range check, before the cache read
  (let ((cached (aref (args-cache a) i)))
    (if (eq cached :unset)
        (setf (aref (args-cache a) i) (eval-node (args-node a i) (args-ctx a)))
        cached)))

;;; For lazy functions re-evaluating a body node under changed bindings.
(defun args-eval (a node) (eval-node node (args-ctx a)))

(defun args-text (a i) (as-text (args-val a i) (args-pos-of a i)))
(defun args-bytes (a i) (as-bytes (args-val a i) (args-pos-of a i)))
(defun args-bool (a i) (as-bool (args-val a i) (args-pos-of a i)))
(defun args-dec (a i) (as-dec (args-val a i) (args-pos-of a i)))

(defun args-int (a i)
  (let ((d (args-dec a i)))
    (unless (dec-integerp d)
      (fail "E_NOT_INT"
            (format nil "~a argument ~d must be a whole number" (args-name a) (1+ i))
            (args-pos-of a i)))
    (dec-to-int d)))

(defun args-non-neg-int (a i)
  (let ((n (args-int a i)))
    (when (minusp n)
      (fail "E_RANGE"
            (format nil "~a argument ~d must not be negative" (args-name a) (1+ i))
            (args-pos-of a i)))
    n))

;;; Requires the argument to be a bare identifier in the source — the AST shape
;;; check that gives aggregates their three-argument binder form.
(defun args-symbol (a i)
  (let ((n (args-node a i)))
    (when (or (not (eq (node-kind n) :var)) (node-grouped n))
      (fail "E_EXPECT_SYMBOL"
            (format nil "~a argument ~d must be a plain name" (args-name a) (1+ i))
            (node-pos n)))
    (node-s n)))

(defun args-symbol-p (a i)
  (let ((n (args-node a i)))
    (and (eq (node-kind n) :var) (not (node-grouped n)))))

;;; --- evaluation ------------------------------------------------------------

(defun eval-node (node ctx)
  (incf (context-depth ctx))
  (when (> (context-depth ctx) +max-depth+)
    (decf (context-depth ctx))
    (fail "E_DEPTH" "evaluation nested too deeply" (node-pos node)))
  (unwind-protect
      (if (node-math-plan node)
          (eval-math-plan (node-math-plan node) ctx)
          (eval-dispatch node ctx))
    (decf (context-depth ctx))))

(defun eval-math-plan (plan ctx)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type math-plan plan) (type context ctx))
  (let* ((size (math-plan-scratchpad-size plan))
         (scratchpad (make-array size :initial-element nil))
         (steps (math-plan-steps plan))
         (num-steps (length steps)))
    (declare (type fixnum size num-steps)
             (type simple-vector scratchpad steps)
             (dynamic-extent scratchpad))
    (macrolet ((opnd (slot pos)
                 ;; A slot holds either a Dec an operation produced or a value a
                 ;; load left as evaluated; the latter is coerced here, where the
                 ;; plain tree coerces it.
                 `(let ((x (svref scratchpad ,slot)))
                    (if (dec-p x) x (as-dec x ,pos)))))
    (loop for i from 0 below num-steps
          do (let* ((step (svref steps i))
                    (op (math-step-op step))
                    (dst (math-step-dst step)))
               (declare (type math-step step)
                        (type fixnum dst)
                        (type keyword op))
               (case op
                 (:load-var
                  (let ((v (ctx-lookup ctx (math-step-name step))))
                    (unless v
                      (fail "E_UNDEF_VAR" (format nil "undefined variable ~a" (math-step-name step))
                            (math-step-pos step)))
                    ;; Stored as evaluated; the operation that consumes it coerces
                    ;; it (SPEC 6.2, evaluate then coerce).
                    (setf (svref scratchpad dst) v)))

                 (:load-const
                  (setf (svref scratchpad dst) (math-step-const-val step)))

                 (:load-leaf
                  (let* ((leaf (math-step-leaf-node step))
                         (v (eval-node leaf ctx)))
                    (setf (svref scratchpad dst) v)))

                 (:coerce
                  (setf (svref scratchpad dst)
                        (opnd (math-step-src1 step) (math-step-pos1 step))))

                 (:add
                  (setf (svref scratchpad dst)
                        (dec-add (opnd (math-step-src1 step) (math-step-pos1 step))
                                 (opnd (math-step-src2 step) (math-step-pos2 step))
                                 (math-step-pos step))))

                 (:sub
                  (setf (svref scratchpad dst)
                        (dec-sub (opnd (math-step-src1 step) (math-step-pos1 step))
                                 (opnd (math-step-src2 step) (math-step-pos2 step))
                                 (math-step-pos step))))

                 (:mul
                  (setf (svref scratchpad dst)
                        (dec-mul (opnd (math-step-src1 step) (math-step-pos1 step))
                                 (opnd (math-step-src2 step) (math-step-pos2 step))
                                 (math-step-pos step))))

                 (:div
                  (setf (svref scratchpad dst)
                        (dec-div (opnd (math-step-src1 step) (math-step-pos1 step))
                                 (opnd (math-step-src2 step) (math-step-pos2 step))
                                 (math-step-pos step))))

                 (:mod
                  (setf (svref scratchpad dst)
                        (dec-mod (opnd (math-step-src1 step) (math-step-pos1 step))
                                 (opnd (math-step-src2 step) (math-step-pos2 step))
                                 (math-step-pos step))))

                 (:neg
                  (setf (svref scratchpad dst)
                        (dec-negate (opnd (math-step-src1 step) (math-step-pos1 step)))))

                 (:abs
                  (setf (svref scratchpad dst)
                        (dec-abs (opnd (math-step-src1 step) (math-step-pos1 step)))))

                 (:sign
                  (let ((s (dec-sign (opnd (math-step-src1 step) (math-step-pos1 step)))))
                    (setf (svref scratchpad dst) (dec-from-int s))))

                 (:ceil
                  (setf (svref scratchpad dst)
                        (dec-ceil (opnd (math-step-src1 step) (math-step-pos1 step)) (math-step-pos step))))

                 (:floor
                  (setf (svref scratchpad dst)
                        (dec-floor (opnd (math-step-src1 step) (math-step-pos1 step)) (math-step-pos step))))

                 (:trunc
                  (setf (svref scratchpad dst)
                        (dec-trunc (opnd (math-step-src1 step) (math-step-pos1 step)))))

                 (:round
                  (let* ((d1 (opnd (math-step-src1 step) (math-step-pos1 step)))
                         (d2 (opnd (math-step-src2 step) (math-step-pos2 step))))
                    (unless (dec-integerp d2)
                      (fail "E_NOT_INT" "ROUND argument 2 must be a whole number" (math-step-aux-pos step)))
                    (let ((n (dec-to-int d2)))
                      (when (minusp n)
                        (fail "E_RANGE" "ROUND argument 2 must not be negative" (math-step-aux-pos step)))
                      (when (> n 1000000)
                        (fail "E_RANGE" (format nil "ROUND scale ~d exceeds the maximum of 1000000" n) (math-step-aux-pos step)))
                      (setf (svref scratchpad dst)
                            (dec-round d1 n (math-step-pos step))))))

                 (:power
                  (let* ((d1 (opnd (math-step-src1 step) (math-step-pos1 step)))
                         (d2 (opnd (math-step-src2 step) (math-step-pos2 step))))
                    (unless (dec-integerp d2)
                      (fail "E_NOT_INT" "POWER argument 2 must be a whole number" (math-step-aux-pos step)))
                    (let ((n (dec-to-int d2)))
                      (when (minusp n)
                        (fail "E_RANGE" "POWER argument 2 must not be negative" (math-step-aux-pos step)))
                      (when (> n 100000)
                        (fail "E_RANGE" (format nil "POWER exponent ~d exceeds the maximum of 100000" n) (math-step-aux-pos step)))
                      (setf (svref scratchpad dst)
                            (dec-power d1 n (math-step-pos step))))))

                 (:min
                  (let ((a (opnd (math-step-src1 step) (math-step-pos1 step)))
                        (b (opnd (math-step-src2 step) (math-step-pos2 step))))
                    (setf (svref scratchpad dst) (if (minusp (dec-cmp b a)) b a))))

                 (:max
                  (let ((a (opnd (math-step-src1 step) (math-step-pos1 step)))
                        (b (opnd (math-step-src2 step) (math-step-pos2 step))))
                    (setf (svref scratchpad dst) (if (plusp (dec-cmp b a)) b a))))))))
    (make-num (svref scratchpad (math-plan-output-slot plan)))))

(defun eval-dispatch (node ctx)
  (declare (optimize (speed 3) (safety 1)))
  (case (node-kind node)
    (:num (%make-value-raw :text (node-s node) nil nil 0 nil nil nil nil (node-dec-val node)))
    (:text (%text (node-s node)))
    (:bool (make-bool (node-b node)))
    (:null (make-null))

    (:var
     (or (ctx-lookup ctx (node-s node))
         (fail "E_UNDEF_VAR" (format nil "undefined variable ~a" (node-s node))
               (node-pos node))))

    (:index
     ;; An index over a bare variable reads the variable in place: the index costs
     ;; its level and the variable none (SPEC 6.4). Every host reads it that way,
     ;; so `A["a"] AND A["a"] AND ...` reaches the cap one level later than
     ;; counting the variable as a node of its own would; the boundary is pinned
     ;; by lim.eval-depth.aggregate-body-*.
     (let* ((l (node-l node))
            (obj (if (eq (node-kind l) :var)
                     (or (ctx-lookup ctx (node-s l))
                         (fail "E_UNDEF_VAR" (format nil "undefined variable ~a" (node-s l))
                               (node-pos l)))
                     (eval-node l ctx)))
            (r (node-r node))
            (key (if (eq (node-kind r) :text)
                     (node-s r)
                     (as-text (eval-node r ctx) (node-pos r))))
            (child (value-get obj key)))
       (or child
           (fail "E_NO_KEY" (format nil "no key ~s" key) (node-pos node)))))

    (:seq
     (let ((last nil))
       (dolist (item (node-items node) last)
         (setf last (eval-node item ctx)))))

    (:list (eval-list node ctx))
    (:un (eval-unary node ctx))
    (:bin (eval-binary node ctx))
    (:assign (eval-assign node ctx))

    (:call
     (let ((a (make-args node ctx)))
       (unless (spec-lazy (node-spec node))
         ;; Strict: every argument evaluated once, left to right, before the body.
         (loop for i from 0 below (args-count a) do (args-val a i)))
       (funcall (spec-fn (node-spec node)) a ctx)))

    ;; The parser makes no other kind: a host error, never a SEL one.
    (t (error "SEL internal error: no evaluation for node kind ~s" (node-kind node)))))

;;; §5.9 — a value with children and no scalar contributes its children's values;
;;; anything else contributes itself. Keys are always renumbered from 1.
(defun eval-list (node ctx)
  (let ((out (make-list-value nil))
        (n 0))
    (dolist (item (node-items node) out)
      (let ((v (eval-node item ctx)))
        ;; The cap counts what the list WILL hold, the flattened children of a
        ;; collection operand included, and is checked before they are copied.
        (check-collection-cap (+ n (max 1 (value-size v))) (node-pos node))
        (if (and (eq (value-kind v) :none) (plusp (value-size v)))
            (dolist (child (value-values v))
              (value-set out (format-index-string (incf n)) (value-copy-at child 2 (node-pos node))))
            (value-set out (format-index-string (incf n)) (value-copy-at v 2 (node-pos node))))))))

(defun eval-unary (node ctx)
  (let ((v (eval-node (node-l node) ctx)))
    (if (string= (node-s node) "NOT")
        (make-bool (not (as-bool v (node-pos (node-l node)))))
        (make-num (dec-negate (as-dec v (node-pos (node-l node))))))))

;;; TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
(defun sel-concat (l r lp rp &optional pos)
  (let ((lv (scalar-source l lp))
        (rv (scalar-source r rp)))
    (when (eq (value-kind lv) :bool) (fail "E_NOT_TEXT" "cannot concatenate a boolean" lp))
    (when (eq (value-kind rv) :bool) (fail "E_NOT_TEXT" "cannot concatenate a boolean" rp))
    (if (and (eq (value-kind lv) :text) (eq (value-kind rv) :text))
        (let ((a (value-scalar lv))
              (b (value-scalar rv)))
          (check-text-cap (+ (length a) (length b)) pos)
          (%text (concatenate 'string a b)))
        (let ((a (as-bytes l lp))
              (b (as-bytes r rp)))
          (check-text-cap (+ (length a) (length b)) pos)
          (make-bin (concatenate '(vector (unsigned-byte 8)) a b))))))

(defun value-in (needle hay)
  (if (zerop (value-size hay))
      (value-eql hay needle)
      (loop for child in (value-values hay)
            thereis (value-eql child needle))))

(defun sel-bitwise (op code a b at)
  (unless (= (length a) (length b))
    (fail "E_LEN_MISMATCH"
          (format nil "~a needs operands of equal length (~d vs ~d)" op (length a) (length b))
          at))
  (make-bin (map '(vector (unsigned-byte 8))
                 (ecase code
                   (:band #'logand)
                   (:bor #'logior)
                   (:bxor #'logxor))
                 a b)))

;;; The evaluator's own keyword for each binary operator. The comparisons take
;;; theirs from the lexicon's relation (one keyword set per family, in the
;;; order eq ne lt le gt ge); the rest are named here. The keywords are this
;;; host's dispatch, not a second vocabulary: the table is checked against
;;; spec/lexicon.json when it is built, so an operator the lexicon has and this
;;; evaluator does not is a load failure, never a run-time :UNKNOWN.
(defparameter +native-op-codes+
  '(("AND" . :and) ("OR" . :or) ("??" . :coalesce) ("???" . :vacuous)
    ("+" . :add) ("-" . :sub) ("*" . :mul) ("/" . :div) ("%" . :mod)
    ("&" . :concat) ("EQL" . :eql) ("IN" . :in) ("XOR" . :xor)
    ("BAND" . :band) ("BOR" . :bor) ("BXOR" . :bxor)))

;;; Every keyword EVAL-BINARY has an arm for.
(defparameter +evaluated-op-codes+
  '(:and :or :coalesce :vacuous :add :sub :mul :div :mod :concat :eql :in :xor
    :band :bor :bxor :eq :ne :lt :le :gt :ge :teq :tne :tlt :tle :tgt :tge))

(defparameter +binary-op-codes+
  (let ((m (make-hash-table :test #'equal)))
    (maphash
     (lambda (token info)
       (when (eq (op-info-node info) :bin)
         (let ((code (case (op-info-family info)
                       (:compare (nth (op-info-relation info) '(:eq :ne :lt :le :gt :ge)))
                       (:text-compare (nth (op-info-relation info) '(:teq :tne :tlt :tle :tgt :tge)))
                       (t (cdr (assoc token +native-op-codes+ :test #'string=))))))
           (unless (and code (member code +evaluated-op-codes+))
             (error "SEL: spec/lexicon.json's operator ~a has no evaluation in eval.lisp" token))
           (setf (gethash token m) code))))
     +infix-op-info+)
    (dolist (pair +native-op-codes+)
      (unless (gethash (car pair) m)
        (error "SEL: eval.lisp evaluates ~a, which spec/lexicon.json does not have" (car pair))))
    m))

(defun binary-op-code (op)
  "The operator's keyword. The one place operator spellings are matched; the
evaluator asks it once per node and dispatches with CASE."
  (gethash op +binary-op-codes+ :unknown))

(declaim (inline node-op-code))
(defun node-op-code (node)
  (or (node-opc node) (setf (node-opc node) (binary-op-code (node-s node)))))

;;; The six comparisons, and nothing else: ECASE, so an operator added to the
;;; families and forgotten here is an error rather than an answer. (The clause
;;; before this function existed was `(t (>= c 0))`, which answered for every
;;; operator it did not name.)
(defun compare-code-result (code c)
  (declare (type fixnum c))
  (ecase code
    ((:eq :teq) (zerop c))
    ((:ne :tne) (not (zerop c)))
    ((:lt :tlt) (minusp c))
    ((:le :tle) (<= c 0))
    ((:gt :tgt) (plusp c))
    ((:ge :tge) (>= c 0))))

(defun eval-binary (node ctx)
  (let ((code (node-op-code node))
        (op (node-s node)))
    (case code
      ;; Short-circuit before either side is touched (§5.5).
      ((:and :or)
       (let ((left (as-bool (eval-node (node-l node) ctx) (node-pos (node-l node)))))
         (cond ((and (eq code :and) (not left)) (make-bool nil))
               ((and (eq code :or) left) (make-bool t))
               (t (make-bool (as-bool (eval-node (node-r node) ctx) (node-pos (node-r node))))))))

      ;; ?? falls back on NULL, ??? on any vacuous value; both on a missing key
      ;; or name.
      ((:coalesce :vacuous)
       (let ((l (handler-case (eval-node (node-l node) ctx)
                  (sel-error (e)
                    (if (or (string= (sel-error-code e) "E_NO_KEY")
                            (string= (sel-error-code e) "E_UNDEF_VAR"))
                        (return-from eval-binary (eval-node (node-r node) ctx))
                        (error e))))))
         (if (if (eq code :coalesce) (value-null-p l) (value-vacuous-p l))
             (eval-node (node-r node) ctx)
             l)))

      (t
       (let* ((l (eval-node (node-l node) ctx))
              (r (eval-node (node-r node) ctx))
              (lp (node-pos (node-l node)))
              (rp (node-pos (node-r node))))
         ;; Each pair of coercions is sequenced left before right: which operand's
         ;; position an error reports is observable, and SEL evaluates strictly
         ;; left to right (§6.2).
         (case code
           ((:add :sub :mul :div :mod)
            (let* ((a (as-dec l lp))
                   (b (as-dec r rp)))
              (make-num (dec-arith code a b (node-pos node)))))

           (:concat (sel-concat l r lp rp (node-pos node)))

           (:eql (make-bool (value-eql l r (node-pos node))))
           (:in (make-bool (value-in l r)))

           (:xor
            (let* ((a (as-bool l lp))
                   (b (as-bool r rp)))
              (make-bool (not (eq a b)))))

           ((:band :bor :bxor)
            (let* ((a (as-bytes l lp))
                   (b (as-bytes r rp)))
              (sel-bitwise op code a b (node-pos node))))

           ((:teq :tne :tlt :tle :tgt :tge)
            (let* ((a (as-bytes l lp))
                   (b (as-bytes r rp)))
              (make-bool (compare-code-result code (bytes-compare a b)))))

           ((:eq :ne :lt :le :gt :ge)
            (let* ((a (as-dec l lp))
                   (b (as-dec r rp)))
              (make-bool (compare-code-result code (dec-cmp a b)))))

           ;; BINARY-OP-CODE knows every operator the parser accepts.
           (t (error "SEL internal error: no evaluation for operator ~a" op))))))))

;;; --- assignment ------------------------------------------------------------

;;; Walks from the root along PATH, creating any level that is missing, and
;;; returns the value at the end. Re-derived rather than remembered — see
;;; RESOLVE-TARGET.
(defun walk-create (ctx path upto)
  (let ((cur (context-root ctx)))
    (loop repeat upto
          for key in path
          do (let ((next (value-get cur key)))
               (unless next
                 (setf next (make-none))
                 (value-set cur key next))
               (setf cur next)))
    cur))

;;; Walks the target chain and returns the full key path, evaluating each index
;;; expression exactly once, left to right, and creating each intermediate level
;;; as it goes — so `A[COUNT(A)] = 1` sees the A the walk just created.
;;;
;;; A path rather than a live container reference (§5.7). The right-hand side may
;;; replace any level the walk just found; the assignment then lands in the tree
;;; that exists afterwards, rather than in a struct that has been detached from
;;; it and which nothing can ever read.
(defun resolve-target (target ctx)
  (let ((chain '())
        (n target))
    (loop while (eq (node-kind n) :index)
          do (push (node-r n) chain)
             (setf n (node-l n)))

    (when (ctx-bound-p ctx (node-s n))
      (fail "E_BAD_ASSIGN"
            (format nil "~a is an aggregate binder and cannot be assigned" (node-s n))
            (node-pos target)))

    ;; The chain was walked iteratively, which is why nothing has counted it yet:
    ;; `A[1][2][3]` is a chain of index nodes, not a nesting of them, so neither
    ;; the parser's depth nor the evaluator's ever sees it -- and the value it is
    ;; about to build is one level deeper than the chain is long. Uncounted, that
    ;; built a value deeper than VALUE-COPY, VALUE-EQL and VALUE-DUMP can walk, so
    ;; the assignment succeeded and reading the result back afterwards failed.
    (when (> (1+ (length chain)) +max-depth+)
      (fail "E_DEPTH" "value nested too deeply" (node-pos target)))

    (let* ((path (list (node-s n)))
           (path-tail path)
           (path-length 1))
      (when (null chain)
        (return-from resolve-target path))

      (unless (value-get (context-root ctx) (node-s n))
        (value-set (context-root ctx) (node-s n) (make-none)))

      (loop for tail on chain
            do (let* ((key-node (first tail))
                      (k (as-text (eval-node key-node ctx) (node-pos key-node))))
                 ;; An index expression can replace an earlier container.
                 ;; Re-resolve the prefix after it runs; never retain CUR.
                 (when (rest tail)
                   (let ((cur (walk-create ctx path path-length)))
                     (unless (value-get cur k)
                       (value-set cur k (make-none)))))
                 ;; PATH is fresh and private: extend it without copying its
                 ;; growing prefix, retaining forward order for re-resolution.
                 (setf (cdr path-tail) (list k)
                       path-tail (cdr path-tail))
                 (incf path-length)))
      path)))

;;; A call whose result is built fresh and referenced by nothing else (SPEC 3.4: these
;;; copy what they collect, and LIST and RECORD copy their arguments). What such a
;;; node yields is private to whoever consumes it, so the consumer adopts it instead
;;; of copying it again: MAP does not copy a RECORD it has just built, a FILTER
;;; or sort fed by a MAP keeps the elements it was handed, and `B = MAP(...)` stores
;;; the list it was given. The copy is only a
;;; defence against aliasing, and there is nothing it could alias. Not in the list:
;;; TAKE, DROP, DISTINCT and DEDUPE (their elements alias the source's), LINK, and
;;; anything that may return an operand as it is (IF, `??`, a variable, an index).
(defparameter +fresh-result-calls+
  '("MAP" "FILTER" "SORT" "SORT_DESC" "SORT_BY" "TOP" "TOP_DESC" "TOP_BY" "BUCKET" "LIST" "RECORD"))

(defun node-fresh-p (node)
  (and node
       (eq (node-kind node) :call)
       (member (node-s node) +fresh-result-calls+ :test #'string=)
       t))

(defun eval-assign (node ctx)
  (let* ((path (resolve-target (node-l node) ctx))
         (key (car (last path)))
         (upto (1- (length path)))
         (value
           (if (string= (node-s node) "=")
               ;; The stored value sits at the end of PATH, so its own nesting
               ;; counts from there: target path plus value depth is what the
               ;; cap bounds (§6.4), reported at the target like the path alone.
               (let ((v (eval-node (node-r node) ctx)))
                 (if (node-fresh-p (node-r node))
                     ;; Built fresh and referenced by nothing else: stored as it is, but
                     ;; its depth still counts from here (the copy did that walk).
                     (progn (value-depth-check v (length path) (node-pos (node-l node)))
                            v)
                     (value-copy-at v (length path) (node-pos (node-l node)))))
               (let ((current (value-get (walk-create ctx path upto) key)))
                 (unless current
                   (fail "E_UNDEF_VAR"
                         (format nil "~a needs an existing target" (node-s node))
                         (node-pos (node-l node))))
                 (let ((rhs (eval-node (node-r node) ctx))
                       ;; The binary operator the compound applies (the lexicon's
                       ;; :compound), as its keyword, cached on the node.
                       (code (or (node-opc node)
                                 (setf (node-opc node)
                                       (binary-op-code (compound-op (node-s node))))))
                       (tp (node-pos (node-l node)))
                       (vp (node-pos (node-r node))))
                   (if (eq code :concat)
                       (sel-concat current rhs tp vp (node-pos node))
                       (let* ((a (as-dec current tp))
                              (b (as-dec rhs vp)))
                         (make-num (dec-arith code a b (node-pos node)))))))))) 
    ;; Re-derived after the right-hand side ran, which may have replaced or
    ;; removed any level along the path.
    (value-set (walk-create ctx path upto) key value)
    value))
