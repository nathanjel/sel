(in-package #:sel)

;;; spec/SPEC.md §6.4. A size argument beyond these asks for more memory than any
;;; host has; capping turns a heap exhaustion into an ordinary rule error. The
;;; underlying arithmetic is exact and unbounded (CL bignums), so these are a
;;; deliberate policy rather than a machine-word limit.
(defconstant +max-scale+ 1000000)
(defconstant +max-power+ 100000)

(defun sized-arg (a i limit what)
  (let ((n (args-non-neg-int a i)))
    (when (> n limit)
      (fail "E_RANGE" (format nil "~a ~d exceeds the maximum of ~d" what n limit)
            (args-pos-of a i)))
    n))

(define-builtin "ABS" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-abs (args-dec a 0)))))
(define-builtin "SIGN" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (dec-sign (args-dec a 0)))))
(define-builtin "CEIL" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-ceil (args-dec a 0)))))
(define-builtin "FLOOR" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-floor (args-dec a 0)))))
(define-builtin "TRUNC" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-trunc (args-dec a 0)))))

(define-builtin "ROUND" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-num (dec-round (args-dec a 0) (sized-arg a 1 +max-scale+ "ROUND scale")))))

(define-builtin "POWER" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-num (dec-power (args-dec a 0) (sized-arg a 1 +max-power+ "POWER exponent")))))

(define-builtin "MIN" 1 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((best (args-dec a 0)))
      (loop for i from 1 below (args-count a)
            for d = (args-dec a i)
            do (when (minusp (dec-cmp d best)) (setf best d)))
      (make-num best))))

(define-builtin "MAX" 1 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((best (args-dec a 0)))
      (loop for i from 1 below (args-count a)
            for d = (args-dec a i)
            do (when (plusp (dec-cmp d best)) (setf best d)))
      (make-num best))))

;;; The non-throwing probe. Every other numeric path raises E_NOT_NUM instead.
(define-builtin "ISNUM" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-bool (looks-numeric (args-val a 0)))))
