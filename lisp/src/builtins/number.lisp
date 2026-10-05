(in-package #:sel)

(defun sized-arg (a i limit what)
  (sized-dec (args-dec a i) (args-name a) (1+ i) limit what (args-pos-of a i)))

(define-builtin "ABS" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-abs (args-dec a 0)))))
(define-builtin "SIGN" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (dec-sign (args-dec a 0)))))
(define-builtin "CEIL" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-ceil (args-dec a 0) (args-pos a)))))
(define-builtin "FLOOR" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-floor (args-dec a 0) (args-pos a)))))
(define-builtin "TRUNC" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-trunc (args-dec a 0)))))
(define-builtin "CANON" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-num (dec-trim-scale (args-dec a 0)))))

(define-builtin "ROUND" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-num (dec-round (args-dec a 0) (sized-arg a 1 +limit-max-round-scale+ "ROUND scale") (args-pos a)))))

(define-builtin "POWER" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-num (dec-power (args-dec a 0) (sized-arg a 1 +limit-max-power-exponent+ "POWER exponent") (args-pos a)))))

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
