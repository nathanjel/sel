;;;; Public host interface. See spec/SPEC.md §8.

(in-package #:sel)

(defstruct (program (:constructor %make-program (source ast)))
  (source "" :type string)
  (ast nil))

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
    (eval-node (program-ast program) (make-context root))))

(defun evaluate (source &optional context)
  (run (compile-source source) context))

;;; --- dependencies ----------------------------------------------------------

(defun collect-deps (node bound reads assigned)
  "BOUND, READS and ASSIGNED are hash tables keyed by name."
  (when node
    (case (node-kind node)
      (:var
       (unless (gethash (node-s node) bound)
         (setf (gethash (node-s node) reads) t)))

      (:assign
       (let ((target (node-l node))
             (n (node-l node)))
         (loop while (eq (node-kind n) :index)
               do (collect-deps (node-r n) bound reads assigned)
                  (setf n (node-l n)))
         ;; `A = x` defines A; `A[k] = x` and `A += x` also read it.
         (when (or (not (eq (node-kind target) :var))
                   (not (string= (node-s node) "=")))
           (unless (gethash (node-s n) bound)
             (setf (gethash (node-s n) reads) t)))
         (setf (gethash (node-s n) assigned) t)
         (collect-deps (node-r node) bound reads assigned)))

      (:call
       ;; An aggregate's three-argument form binds its second argument as a name
       ;; for the duration of the third.
       (let ((spec (node-spec node))
             (items (node-items node)))
         (cond
           ((and spec (spec-binds spec) (= (length items) 3)
                 (eq (node-kind (second items)) :var))
            (collect-deps (first items) bound reads assigned)
            (let ((inner (copy-name-table bound)))
              (setf (gethash (node-s (second items)) inner) t)
              (setf (gethash "_K" inner) t)
              (collect-deps (third items) inner reads assigned)))
           ((and spec (spec-binds spec) (= (length items) 2))
            (collect-deps (first items) bound reads assigned)
            (let ((inner (copy-name-table bound)))
              (setf (gethash "_" inner) t)
              (setf (gethash "_K" inner) t)
              (collect-deps (second items) inner reads assigned)))
           (t (dolist (arg items) (collect-deps arg bound reads assigned))))))

      ((:seq :list)
       (dolist (item (node-items node)) (collect-deps item bound reads assigned)))

      ((:index :bin)
       (collect-deps (node-l node) bound reads assigned)
       (collect-deps (node-r node) bound reads assigned))

      (:un (collect-deps (node-l node) bound reads assigned))

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
    (collect-deps (program-ast program) bound reads assigned)
    (sort (loop for name being the hash-keys of reads
                unless (gethash name assigned) collect name)
          #'string<)))
