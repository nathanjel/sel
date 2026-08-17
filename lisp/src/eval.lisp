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
  (depth 0 :type fixnum))

(defun ctx-lookup (ctx name)
  (loop for frame in (context-frames ctx)
        do (let ((cell (assoc name frame :test #'string=)))
             (when cell (return-from ctx-lookup (cdr cell)))))
  (value-get (context-root ctx) name))

(defun ctx-bound-p (ctx name)
  (loop for frame in (context-frames ctx)
        thereis (and (assoc name frame :test #'string=) t)))

(defun ctx-push-frame (ctx frame) (push frame (context-frames ctx)))
(defun ctx-pop-frame (ctx) (pop (context-frames ctx)))

;;; --- arguments -------------------------------------------------------------

;;; Wraps the flattened argument vector. Values are evaluated at most once, so a
;;; built-in body can read the same argument repeatedly without thinking about
;;; it, and typed accessors report failures against the argument's own position.
(defstruct (args (:constructor %make-args (nodes name pos ctx cache)))
  (nodes nil :type list)
  (name "" :type string)
  (pos nil)
  (ctx nil)
  (cache #() :type vector))

(defun make-args (node ctx)
  (%make-args (node-items node) (node-s node) (node-pos node) ctx
              (make-array (length (node-items node)) :initial-element :unset)))

(defun args-count (a) (length (args-nodes a)))
(defun args-node (a i) (nth i (args-nodes a)))
(defun args-pos-of (a i) (node-pos (args-node a i)))

(defun args-val (a i)
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

;;; --- evaluation ------------------------------------------------------------

(defun eval-node (node ctx)
  (incf (context-depth ctx))
  (when (> (context-depth ctx) +max-depth+)
    (decf (context-depth ctx))
    (fail "E_DEPTH" "evaluation nested too deeply" (node-pos node)))
  (unwind-protect (eval-dispatch node ctx)
    (decf (context-depth ctx))))

(defun eval-dispatch (node ctx)
  (case (node-kind node)
    (:num (%text (node-s node)))          ; canonicalised by the parser
    (:text (%text (node-s node)))
    (:bool (make-bool (node-b node)))

    (:var
     (or (ctx-lookup ctx (node-s node))
         (fail "E_UNDEF_VAR" (format nil "undefined variable ~a" (node-s node))
               (node-pos node))))

    (:index
     (let* ((obj (eval-node (node-l node) ctx))
            (key (as-text (eval-node (node-r node) ctx) (node-pos (node-r node))))
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

    (t (fail "E_SYNTAX" "cannot evaluate node" (node-pos node)))))

;;; §5.9 — a value with children and no scalar contributes its children's values;
;;; anything else contributes itself. Keys are always renumbered from 1.
(defun eval-list (node ctx)
  (let ((out (make-none))
        (n 0))
    (dolist (item (node-items node) out)
      (let ((v (eval-node item ctx)))
        (if (and (eq (value-kind v) :none) (plusp (value-size v)))
            (dolist (child (value-values v))
              (value-set out (format nil "~d" (incf n)) (value-copy child)))
            (value-set out (format nil "~d" (incf n)) (value-copy v)))))))

(defun eval-unary (node ctx)
  (let ((v (eval-node (node-l node) ctx)))
    (if (string= (node-s node) "NOT")
        (make-bool (not (as-bool v (node-pos (node-l node)))))
        (make-num (dec-negate (as-dec v (node-pos (node-l node))))))))

(defun compare-result (op c)
  (cond ((string= op "==") (zerop c))
        ((string= op "!=") (not (zerop c)))
        ((string= op "<") (minusp c))
        ((string= op "<=") (<= c 0))
        ((string= op ">") (plusp c))
        (t (>= c 0))))

;;; TEXT & TEXT stays TEXT; anything involving BIN becomes BIN (§5.2).
(defun sel-concat (l r lp rp)
  (let ((lv (scalar-source l lp))
        (rv (scalar-source r rp)))
    (when (eq (value-kind lv) :bool) (fail "E_NOT_TEXT" "cannot concatenate a boolean" lp))
    (when (eq (value-kind rv) :bool) (fail "E_NOT_TEXT" "cannot concatenate a boolean" rp))
    (if (and (eq (value-kind lv) :text) (eq (value-kind rv) :text))
        (%text (concatenate 'string (value-scalar lv) (value-scalar rv)))
        (let ((a (as-bytes l lp))
              (b (as-bytes r rp)))
          (make-bin (concatenate '(vector (unsigned-byte 8)) a b))))))

(defun value-in (needle hay)
  (if (zerop (value-size hay))
      (value-eql hay needle)
      (loop for child in (value-values hay)
            thereis (value-eql child needle))))

(defun sel-bitwise (op a b at)
  (unless (= (length a) (length b))
    (fail "E_LEN_MISMATCH"
          (format nil "~a needs operands of equal length (~d vs ~d)" op (length a) (length b))
          at))
  (make-bin (map '(vector (unsigned-byte 8))
                 (cond ((string= op "BAND") #'logand)
                       ((string= op "BOR") #'logior)
                       (t #'logxor))
                 a b)))

(defun eval-binary (node ctx)
  (let ((op (node-s node)))
    ;; Short-circuit before either side is touched (§5.5).
    (when (or (string= op "AND") (string= op "OR"))
      (let ((left (as-bool (eval-node (node-l node) ctx) (node-pos (node-l node)))))
        (when (and (string= op "AND") (not left)) (return-from eval-binary (make-bool nil)))
        (when (and (string= op "OR") left) (return-from eval-binary (make-bool t)))
        (return-from eval-binary
          (make-bool (as-bool (eval-node (node-r node) ctx) (node-pos (node-r node)))))))

    (let* ((l (eval-node (node-l node) ctx))
           (r (eval-node (node-r node) ctx))
           (lp (node-pos (node-l node)))
           (rp (node-pos (node-r node))))
      (cond
        ;; Each pair of coercions is sequenced left before right: which operand's
        ;; position an error reports is observable, and SEL evaluates strictly
        ;; left to right (§6.2).
        ((member op '("+" "-" "*" "/" "%") :test #'string=)
         (let* ((a (as-dec l lp))
                (b (as-dec r rp)))
           (make-num (cond ((string= op "+") (dec-add a b))
                           ((string= op "-") (dec-sub a b))
                           ((string= op "*") (dec-mul a b))
                           ((string= op "/") (dec-div a b (node-pos node)))
                           (t (dec-mod a b (node-pos node)))))))

        ((string= op "&") (sel-concat l r lp rp))

        ((string= op "EQL") (make-bool (value-eql l r)))
        ((string= op "IN") (make-bool (value-in l r)))

        ((string= op "XOR")
         (let* ((a (as-bool l lp))
                (b (as-bool r rp)))
           (make-bool (not (eq a b)))))

        ((member op '("BAND" "BOR" "BXOR") :test #'string=)
         (let* ((a (as-bytes l lp))
                (b (as-bytes r rp)))
           (sel-bitwise op a b (node-pos node))))

        ((char= (char op 0) #\$)
         (let* ((a (as-bytes l lp))
                (b (as-bytes r rp)))
           (make-bool (compare-result (subseq op 1) (bytes-compare a b)))))

        ((member op +compare-ops+ :test #'string=)
         (let* ((a (as-dec l lp))
                (b (as-dec r rp)))
           (make-bool (compare-result op (dec-cmp a b)))))

        (t (fail "E_SYNTAX" (format nil "unknown operator ~a" op) (node-pos node)))))))

;;; --- assignment ------------------------------------------------------------

;;; Walks from the root along PATH, creating any level that is missing, and
;;; returns the value at the end. Re-derived rather than remembered — see
;;; RESOLVE-TARGET.
(defun walk-create (ctx path upto)
  (let ((cur (context-root ctx)))
    (loop for i from 0 below upto
          do (let ((next (value-get cur (nth i path))))
               (unless next
                 (setf next (make-none))
                 (value-set cur (nth i path) next))
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

    (let ((path (list (node-s n))))
      (when (null chain)
        (return-from resolve-target path))

      (unless (value-get (context-root ctx) (node-s n))
        (value-set (context-root ctx) (node-s n) (make-none)))

      (loop for tail on chain
            while (rest tail)
            do (let* ((key-node (first tail))
                      (k (as-text (eval-node key-node ctx) (node-pos key-node)))
                      (cur (walk-create ctx path (length path))))
                 (unless (value-get cur k)
                   (value-set cur k (make-none)))
                 (setf path (append path (list k)))))
      (let ((last (car (last chain))))
        (append path (list (as-text (eval-node last ctx) (node-pos last))))))))

(defun eval-assign (node ctx)
  (let* ((path (resolve-target (node-l node) ctx))
         (key (car (last path)))
         (upto (1- (length path)))
         (value
           (if (string= (node-s node) "=")
               (value-copy (eval-node (node-r node) ctx))
               (let ((current (value-get (walk-create ctx path upto) key)))
                 (unless current
                   (fail "E_UNDEF_VAR"
                         (format nil "~a needs an existing target" (node-s node))
                         (node-pos (node-l node))))
                 (let ((rhs (eval-node (node-r node) ctx))
                       (binop (char (node-s node) 0))
                       (tp (node-pos (node-l node)))
                       (vp (node-pos (node-r node))))
                   (if (char= binop #\&)
                       (sel-concat current rhs tp vp)
                       (let* ((a (as-dec current tp))
                              (b (as-dec rhs vp)))
                         (make-num (case binop
                                     (#\+ (dec-add a b))
                                     (#\- (dec-sub a b))
                                     (#\* (dec-mul a b))
                                     (#\/ (dec-div a b (node-pos node)))
                                     (t (dec-mod a b (node-pos node)))))))))))) 
    ;; Re-derived after the right-hand side ran, which may have replaced or
    ;; removed any level along the path.
    (value-set (walk-create ctx path upto) key value)
    value))
