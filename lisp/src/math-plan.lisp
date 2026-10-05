;;;; math-plan.lisp - Linear 3-address execution plan for arithmetic subtrees.
;;;;
;;;; Bypasses recursive AST evaluation and value boxing for pure arithmetic
;;;; operations by executing a flat sequence of steps over a pre-allocated
;;;; scratchpad of slots, holding Dec structures and, for the load steps
;;;; (load-var, load-leaf), the raw VALUEs they read.

(in-package #:sel)

;;; The vocabulary -- which source nodes compile, to which operation, with how
;;; many operands and which error positions -- is spec/math-ops.json's, rendered
;;; into math-ops.lisp. The opcode KEYWORDS are this host's own: the executor in
;;; eval.lisp dispatches on the ones below, and loading refuses to continue if
;;; the manifest names an operation the executor lacks.
(defparameter +math-executor-ops+
  '(:add :sub :mul :div :mod :neg :abs :sign :ceil :floor :trunc :round :power :min :max))

(defun math-op-keyword (name)
  (let ((kw (intern name :keyword)))
    (unless (member kw +math-executor-ops+)
      (error "spec/math-ops.json names ~a, which this host's math plan has no opcode for" name))
    kw))

(dolist (entry *math-op-data*) (math-op-keyword (first entry)))

(defun math-op-entry (kind token)
  "The manifest entry (name kind token arity aux) for a source node, or NIL."
  (find-if (lambda (e) (and (eq (second e) kind) (string= (third e) token))) *math-op-data*))

(defparameter +math-binary-ops+
  (loop for (nil kind token) in *math-op-data* when (eq kind :operator) collect token))
(defparameter +math-unary-ops+
  (loop for (nil kind token) in *math-op-data* when (eq kind :prefix) collect token))
(defparameter +math-builtins+
  (loop for (nil kind token) in *math-op-data* when (eq kind :builtin) collect token))

(defstruct (math-step (:constructor make-math-step (op dst &key (src1 0) (src2 0) pos aux-pos pos1 pos2 (name "") const-val leaf-node)))
  (op :add :type keyword)
  (dst 0 :type fixnum)
  (src1 0 :type fixnum)
  (src2 0 :type fixnum)
  pos
  aux-pos
  ;; The positions of the operand NODES, for the coercion an operation does when
  ;; it runs. A load stores the value as evaluated and coerces nothing (SPEC 6.2,
  ;; evaluate then coerce): the operation coerces its operands, left first, once
  ;; every operand has been evaluated, exactly as the plain tree does.
  pos1
  pos2
  (name "" :type string)
  const-val
  leaf-node)

(defstruct (math-plan (:constructor make-math-plan (steps output-slot scratchpad-size)))
  (steps #() :type simple-vector)
  (output-slot 0 :type fixnum)
  (scratchpad-size 0 :type fixnum))

(defun is-math-op-p (node)
  (and (node-p node)
       (case (node-kind node)
         (:bin (member (node-s node) +math-binary-ops+ :test #'string=))
         (:un (member (node-s node) +math-unary-ops+ :test #'string=))
         (:call (member (node-s node) +math-builtins+ :test #'string=))
         (t nil))))

(defun compile-math-plan (root)
  (unless (is-math-op-p root)
    (return-from compile-math-plan nil))

  (let ((steps nil)
        (slot-count 0)
        (raw-slots nil))     ; slots holding a value as evaluated, not yet coerced
    (labels ((alloc-slot ()
               (prog1 slot-count
                 (incf slot-count)))
             (raw-p (slot) (member slot raw-slots))
             ;; A slot handed on WITHOUT an operation (`x + 0` is `x`) must still
             ;; be coerced where the operation would have coerced it, or a later
             ;; operand's error would be found first.
             (coerced (slot pos)
               (if (raw-p slot)
                   (let ((dst (alloc-slot)))
                     (push (make-math-step :coerce dst :src1 slot :pos1 pos) steps)
                     dst)
                   slot))
             (emit (node depth)
               (when (> depth +max-depth+)
                 (return-from compile-math-plan nil))
               (case (node-kind node)
                 (:var
                  (let ((slot (alloc-slot)))
                    (push (make-math-step :load-var slot
                                          :name (node-s node)
                                          :pos (node-pos node))
                          steps)
                    (push slot raw-slots)
                    (values slot nil)))

                 (:num
                  (let ((d (or (node-dec-val node)
                               (handler-case (dec-parse (node-s node) (node-pos node))
                                 (error () (return-from compile-math-plan nil))))))
                    (unless d (return-from compile-math-plan nil))
                    (let ((slot (alloc-slot)))
                      (push (make-math-step :load-const slot
                                            :const-val d
                                            :pos (node-pos node))
                            steps)
                      (values slot d))))

                 (:bin
                  (if (member (node-s node) +math-binary-ops+ :test #'string=)
                      (progn
                        (unless (and (node-l node) (node-r node))
                          (return-from compile-math-plan nil))
                        (multiple-value-bind (slot-l const-l) (emit (node-l node) (1+ depth))
                          (multiple-value-bind (slot-r const-r) (emit (node-r node) (1+ depth))
                            (let ((op (node-s node)))
                              ;; Copy propagation:
                              ;; Rule 1: x + 0 (scale == 0) -> slot-l
                              (when (and (string= op "+")
                                         const-r
                                         (dec-zerop const-r)
                                         (zerop (dec-scale const-r)))
                                (when (and (eq (node-kind (node-r node)) :num)
                                           steps
                                           (= (math-step-dst (first steps)) slot-r))
                                  (pop steps))
                                (return-from emit (values (coerced slot-l (node-pos (node-l node))) const-l)))

                              ;; Rule 2: 0 + x (scale == 0) -> slot-r
                              (when (and (string= op "+")
                                         const-l
                                         (dec-zerop const-l)
                                         (zerop (dec-scale const-l)))
                                (return-from emit (values (coerced slot-r (node-pos (node-r node))) const-r)))

                              ;; Rule 3: x - 0 (scale == 0) -> slot-l
                              (when (and (string= op "-")
                                         const-r
                                         (dec-zerop const-r)
                                         (zerop (dec-scale const-r)))
                                (when (and (eq (node-kind (node-r node)) :num)
                                           steps
                                           (= (math-step-dst (first steps)) slot-r))
                                  (pop steps))
                                (return-from emit (values (coerced slot-l (node-pos (node-l node))) const-l)))

                              ;; Rule 4: x * 1 (scale == 0) -> slot-l
                              (when (and (string= op "*")
                                         const-r
                                         (not (dec-neg const-r))
                                         (= (dec-digits const-r) 1)
                                         (zerop (dec-scale const-r)))
                                (when (and (eq (node-kind (node-r node)) :num)
                                           steps
                                           (= (math-step-dst (first steps)) slot-r))
                                  (pop steps))
                                (return-from emit (values (coerced slot-l (node-pos (node-l node))) const-l)))

                              ;; Rule 5: 1 * x (scale == 0) -> slot-r
                              (when (and (string= op "*")
                                         const-l
                                         (not (dec-neg const-l))
                                         (= (dec-digits const-l) 1)
                                         (zerop (dec-scale const-l)))
                                (return-from emit (values (coerced slot-r (node-pos (node-r node))) const-r)))

                              (let ((dst (alloc-slot))
                                    (opcode (math-op-keyword (first (math-op-entry :operator op)))))
                                (push (make-math-step opcode dst
                                                      :src1 slot-l
                                                      :src2 slot-r
                                                      :pos (node-pos node)
                                                      :pos1 (node-pos (node-l node))
                                                      :pos2 (node-pos (node-r node)))
                                      steps)
                                (values dst nil))))))
                      (return-from compile-math-plan nil)))

                 (:un
                  (if (member (node-s node) +math-unary-ops+ :test #'string=)
                      (progn
                        (unless (node-l node) (return-from compile-math-plan nil))
                        (multiple-value-bind (slot-x const-x) (emit (node-l node) (1+ depth))
                          (declare (ignore const-x))
                          (let ((dst (alloc-slot)))
                            (push (make-math-step (math-op-keyword (first (math-op-entry :prefix (node-s node)))) dst
                                                  :src1 slot-x
                                                  :pos (node-pos node)
                                                  :pos1 (node-pos (node-l node)))
                                  steps)
                            (values dst nil))))
                      (return-from compile-math-plan nil)))

                 (:call
                  ;; Math builtins: operand count, fold and error positions from
                  ;; the manifest entry; a count the entry cannot serve derails
                  ;; the plan.
                  (let* ((name (node-s node))
                         (args (node-items node))
                         (entry (math-op-entry :builtin name)))
                    (cond
                      (entry
                       (destructuring-bind (op-name kind token arity aux) entry
                         (declare (ignore kind token))
                         (let ((opcode (math-op-keyword op-name)))
                           (cond
                             ((eql arity 1)
                              (unless (= (length args) 1) (return-from compile-math-plan nil))
                              (multiple-value-bind (slot-arg const-arg) (emit (first args) (1+ depth))
                                (declare (ignore const-arg))
                                (let ((dst (alloc-slot)))
                                  (push (make-math-step opcode dst :src1 slot-arg :pos (node-pos node)
                                                              :pos1 (node-pos (first args)))
                                        steps)
                                  (values dst nil))))
                             ((eql arity 2)
                              (unless (= (length args) 2) (return-from compile-math-plan nil))
                              (multiple-value-bind (slot-0 const-0) (emit (first args) (1+ depth))
                                (declare (ignore const-0))
                                (multiple-value-bind (slot-1 const-1) (emit (second args) (1+ depth))
                                  (declare (ignore const-1))
                                  (let ((dst (alloc-slot)))
                                    (push (make-math-step opcode dst
                                                          :src1 slot-0 :src2 slot-1
                                                          :pos (node-pos node)
                                                          :pos1 (node-pos (first args))
                                                          :pos2 (node-pos (second args))
                                                          :aux-pos (and aux (node-pos (nth aux args))))
                                          steps)
                                    (values dst nil)))))
                             (t ; fold: one or more operands, combined pairwise left to right
                              (unless (>= (length args) 1) (return-from compile-math-plan nil))
                              ;; Every argument is evaluated before any is coerced (SPEC
                              ;; 7.1), so all of them load first and the pairwise chain runs
                              ;; over the slots.
                              (let ((slots (loop for arg in args
                                                 collect (values (emit arg (1+ depth)))))
                                    (curr-slot nil))
                                (setf curr-slot (if (rest args)
                                                    (first slots)
                                                    (coerced (first slots) (node-pos (first args)))))
                                (loop for arg in (rest args)
                                      for next-slot in (rest slots)
                                      do (let ((dst (alloc-slot)))
                                           (push (make-math-step opcode dst
                                                                 :src1 curr-slot :src2 next-slot
                                                                 :pos (node-pos node)
                                                                 :pos1 (node-pos (first args))
                                                                 :pos2 (node-pos arg))
                                                 steps)
                                           (setf curr-slot dst)))
                                (values curr-slot nil)))))))

                      ((member name '("IF" "COND") :test #'string=)
                       (return-from compile-math-plan nil))

                      (t
                       (let ((slot (alloc-slot)))
                         (push (make-math-step :load-leaf slot
                                               :leaf-node node
                                               :pos (node-pos node))
                               steps)
                         (push slot raw-slots)
                         (values slot nil))))))

                 ((:assign :seq :list)
                  (return-from compile-math-plan nil))

                 (t
                  (let ((slot (alloc-slot)))
                    (push (make-math-step :load-leaf slot
                                          :leaf-node node
                                          :pos (node-pos node))
                          steps)
                    (push slot raw-slots)
                    (values slot nil))))))

      (multiple-value-bind (output-slot const-root) (emit root 1)
        (declare (ignore const-root))
        (setf output-slot (coerced output-slot (node-pos root)))
        (unless steps (return-from compile-math-plan nil))
        (make-math-plan (coerce (nreverse steps) 'simple-vector)
                        output-slot
                        slot-count)))))
