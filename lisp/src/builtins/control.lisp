;;;; The whole of SEL's control flow. Lazy, so only the taken branch is
;;;; evaluated — exactly the property the AST calling convention exists to
;;;; provide.

(in-package #:sel)

(define-builtin "IF" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (cond ((args-bool a 0) (args-val a 1))
          ((= (args-count a) 3) (args-val a 2))
          (t (%text ""))))
  :lazy t)

;;; Flat multi-branch selection, with exactly IF's laziness: conditions are
;;; evaluated in order, and only the result that matches is evaluated at all.
;;;
;;; The argument count must be odd — condition/result pairs plus a mandatory
;;; default. IF can safely let its two-argument form default to "" because there
;;; is one branch and nothing to mis-pair, but with an even count here a single
;;; miscounted comma would shift every pair by one and still compile. Requiring
;;; the default turns that into a compile-time E_ARITY rather than a wrong answer
;;; at run time.
(define-builtin "COND" 3 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((last (1- (args-count a))))
      (loop for i from 0 below last by 2
            do (when (args-bool a i) (return (args-val a (1+ i))))
            finally (return (args-val a last)))))
  :lazy t
  :arity-error (lambda (n)
                 (when (evenp n)
                   (format nil "COND takes condition/result pairs and a final default (an odd number of arguments), got ~d" n))))

;;; The one error a rule author raises deliberately.
(define-builtin "ABORT" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (fail "E_ABORT" (args-text a 0) (args-pos-of a 0))))
