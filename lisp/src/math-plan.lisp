;;;; math-plan.lisp - Linear 3-address execution plan for arithmetic subtrees.
;;;;
;;;; Bypasses recursive AST evaluation and value boxing for pure arithmetic
;;;; operations by executing a flat sequence of steps over a pre-allocated
;;;; scratchpad of Dec structures.

(in-package #:sel)

(defparameter +math-binary-ops+ '("+" "-" "*" "/" "%"))
(defparameter +math-unary-ops+ '("NEG"))
(defparameter +math-builtins+ '("ROUND" "ABS" "SIGN" "CEIL" "FLOOR" "TRUNC" "POWER" "MIN" "MAX"))

(defstruct (math-step (:constructor make-math-step (op dst &key (src1 0) (src2 0) pos aux-pos (name "") const-val leaf-node)))
  (op :add :type keyword)
  (dst 0 :type fixnum)
  (src1 0 :type fixnum)
  (src2 0 :type fixnum)
  pos
  aux-pos
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
        (slot-count 0))
    (labels ((alloc-slot ()
               (prog1 slot-count
                 (incf slot-count)))
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
                                (return-from emit (values slot-l const-l)))

                              ;; Rule 2: 0 + x (scale == 0) -> slot-r
                              (when (and (string= op "+")
                                         const-l
                                         (dec-zerop const-l)
                                         (zerop (dec-scale const-l)))
                                (return-from emit (values slot-r const-r)))

                              ;; Rule 3: x - 0 (scale == 0) -> slot-l
                              (when (and (string= op "-")
                                         const-r
                                         (dec-zerop const-r)
                                         (zerop (dec-scale const-r)))
                                (when (and (eq (node-kind (node-r node)) :num)
                                           steps
                                           (= (math-step-dst (first steps)) slot-r))
                                  (pop steps))
                                (return-from emit (values slot-l const-l)))

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
                                (return-from emit (values slot-l const-l)))

                              ;; Rule 5: 1 * x (scale == 0) -> slot-r
                              (when (and (string= op "*")
                                         const-l
                                         (not (dec-neg const-l))
                                         (= (dec-digits const-l) 1)
                                         (zerop (dec-scale const-l)))
                                (return-from emit (values slot-r const-r)))

                              (let ((dst (alloc-slot))
                                    (opcode (cond ((string= op "+") :add)
                                                  ((string= op "-") :sub)
                                                  ((string= op "*") :mul)
                                                  ((string= op "/") :div)
                                                  (t :mod))))
                                (push (make-math-step opcode dst
                                                      :src1 slot-l
                                                      :src2 slot-r
                                                      :pos (node-pos node))
                                      steps)
                                (values dst nil))))))
                      (return-from compile-math-plan nil)))

                 (:un
                  (if (string= (node-s node) "NEG")
                      (progn
                        (unless (node-l node) (return-from compile-math-plan nil))
                        (multiple-value-bind (slot-x const-x) (emit (node-l node) (1+ depth))
                          (declare (ignore const-x))
                          (let ((dst (alloc-slot)))
                            (push (make-math-step :neg dst
                                                  :src1 slot-x
                                                  :pos (node-pos node))
                                  steps)
                            (values dst nil))))
                      (return-from compile-math-plan nil)))

                 (:call
                  (let ((name (node-s node))
                        (args (node-items node)))
                    (cond
                      ((member name '("ABS" "SIGN" "CEIL" "FLOOR" "TRUNC") :test #'string=)
                       (unless (= (length args) 1) (return-from compile-math-plan nil))
                       (multiple-value-bind (slot-arg const-arg) (emit (first args) (1+ depth))
                         (declare (ignore const-arg))
                         (let ((dst (alloc-slot))
                               (opcode (cond ((string= name "ABS") :abs)
                                             ((string= name "SIGN") :sign)
                                             ((string= name "CEIL") :ceil)
                                             ((string= name "FLOOR") :floor)
                                             (t :trunc))))
                           (push (make-math-step opcode dst
                                                 :src1 slot-arg
                                                 :pos (node-pos node))
                                 steps)
                           (values dst nil))))

                      ((member name '("ROUND" "POWER") :test #'string=)
                       (unless (= (length args) 2) (return-from compile-math-plan nil))
                       (multiple-value-bind (slot-0 const-0) (emit (first args) (1+ depth))
                         (declare (ignore const-0))
                         (multiple-value-bind (slot-1 const-1) (emit (second args) (1+ depth))
                           (declare (ignore const-1))
                           (let ((dst (alloc-slot))
                                 (opcode (if (string= name "ROUND") :round :power)))
                             (push (make-math-step opcode dst
                                                   :src1 slot-0
                                                   :src2 slot-1
                                                   :pos (node-pos node)
                                                   :aux-pos (node-pos (second args)))
                                   steps)
                             (values dst nil)))))

                      ((member name '("MIN" "MAX") :test #'string=)
                       (unless (>= (length args) 1) (return-from compile-math-plan nil))
                       (multiple-value-bind (curr-slot const-0) (emit (first args) (1+ depth))
                         (declare (ignore const-0))
                         (let ((opcode (if (string= name "MIN") :min :max)))
                           (loop for arg in (rest args)
                                 do (multiple-value-bind (next-slot const-next) (emit arg (1+ depth))
                                      (declare (ignore const-next))
                                      (let ((dst (alloc-slot)))
                                        (push (make-math-step opcode dst
                                                              :src1 curr-slot
                                                              :src2 next-slot
                                                              :pos (node-pos node))
                                              steps)
                                        (setf curr-slot dst))))
                           (values curr-slot nil))))

                      ((member name '("IF" "COND") :test #'string=)
                       (return-from compile-math-plan nil))

                      (t
                       (let ((slot (alloc-slot)))
                         (push (make-math-step :load-leaf slot
                                               :leaf-node node
                                               :pos (node-pos node))
                               steps)
                         (values slot nil))))))

                 ((:assign :seq :list)
                  (return-from compile-math-plan nil))

                 (t
                  (let ((slot (alloc-slot)))
                    (push (make-math-step :load-leaf slot
                                          :leaf-node node
                                          :pos (node-pos node))
                          steps)
                    (values slot nil))))))

      (multiple-value-bind (output-slot const-root) (emit root 1)
        (declare (ignore const-root))
        (unless steps (return-from compile-math-plan nil))
        (make-math-plan (coerce (nreverse steps) 'simple-vector)
                        output-slot
                        slot-count)))))
