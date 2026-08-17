(in-package #:sel)

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
