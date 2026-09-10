(in-package #:sel)

;;; A scalar with no children behaves as a one-element list containing itself,
;;; consistent with scalar context (§3.2). A NONE with no children is genuinely
;;; empty — that is what FILTER returns when nothing matched, and ALL over it
;;; must be TRUE rather than a scalar-context failure.
(defun aggregate-elements (v)
  (cond ((plusp (value-size v)) (value-entries v))
        ((eq (value-kind v) :none) '())
        (t (list (cons "1" v)))))

(define-builtin "COUNT" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (value-size (args-val a 0)))))

(define-builtin "INDEXES" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value (mapcar #'%text (value-keys (args-val a 0))))))

(define-builtin "HAS" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (value-has (args-val a 0) (args-text a 1)))))

(define-builtin "LIST" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value
     (loop for i below (args-count a)
           collect (value-copy (args-val a i))))))

(define-builtin "RECORD" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((rec (make-none))
          (n (args-count a)))
      (loop for i from 0 below n by 2
            do (value-set rec (args-text a i) (value-copy (args-val a (1+ i)))))
      rec))
  :arity-error (lambda (count)
                 (when (oddp count)
                   (format nil "RECORD takes an even number of arguments (key-value pairs), got ~d" count))))

(define-builtin "TAKE" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0))
          (count (args-non-neg-int a 1)))
      (if (or (zerop count) (value-null-p val))
          (make-list-value nil)
          (let* ((ents (aggregate-elements val))
                 (limit (min count (length ents))))
            (make-list-value
             (loop for cell in (subseq ents 0 limit)
                   collect (value-copy (cdr cell)))))))))

(define-builtin "DROP" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0))
          (count (args-non-neg-int a 1)))
      (if (value-null-p val)
          (make-list-value nil)
          (let ((ents (aggregate-elements val)))
            (if (>= count (length ents))
                (make-list-value nil)
                (make-list-value
                 (loop for cell in (nthcdr count ents)
                       collect (value-copy (cdr cell))))))))))

(define-builtin "SELECT_COLS" 2 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0)))
      (if (value-null-p val)
          (make-list-value nil)
          (let* ((col-count (args-count a))
                 (cols (loop for i from 1 below col-count collect (args-text a i)))
                 (ents (aggregate-elements val)))
            (make-list-value
             (loop for (nil . row) in ents
                   collect
                   (let ((new-row (make-none)))
                     (dolist (c cols)
                       (when (value-has row c)
                         (value-set new-row c (value-copy (value-get row c)))))
                     new-row))))))))

(define-builtin "DISTINCT" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0)))
      (if (value-null-p val)
          (make-list-value nil)
          (let ((ents (aggregate-elements val))
                (out '()))
            (loop for (nil . item) in ents
                  unless (member item out :test #'value-eql)
                    do (push (value-copy item) out))
            (make-list-value (nreverse out)))))))
