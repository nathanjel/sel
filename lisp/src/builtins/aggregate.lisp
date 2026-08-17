;;;; Aggregates. These are why SEL needs no loop: each evaluates one argument
;;;; node once per element, which is the same move IF makes, repeated.

(in-package #:sel)

;;; A scalar with no children behaves as a one-element list containing itself,
;;; consistent with scalar context (§3.2). A NONE with no children is genuinely
;;; empty — that is what FILTER returns when nothing matched, and ALL over it
;;; must be TRUE rather than a scalar-context failure.
(defun aggregate-elements (v)
  (cond ((plusp (value-size v)) (value-entries v))
        ((eq (value-kind v) :none) '())
        (t (list (cons "1" v)))))

;;; Runs VISIT per element with the binder and _K in scope. A non-NIL return from
;;; VISIT stops the walk and becomes the result.
(defun aggregate-walk (a ctx visit)
  (let* ((three (= (args-count a) 3))
         (binder (if three (args-symbol a 1) "_"))
         (body (args-node a (if three 2 1))))
    (loop for (key . item) in (aggregate-elements (args-val a 0))
          do (ctx-push-frame ctx (list (cons binder item)
                                       (cons "_K" (%text key))))
             (let ((result
                     (unwind-protect
                          (funcall visit (args-eval a body) key item body)
                       (ctx-pop-frame ctx))))
               (when result (return result))))))

(define-builtin "ALL" 2 3
  (lambda (a ctx)
    (or (aggregate-walk a ctx
                        (lambda (r key item body)
                          (declare (ignore key item))
                          (unless (as-bool r (node-pos body)) (make-bool nil))))
        (make-bool t)))
  :lazy t :binds t)

(define-builtin "ANY" 2 3
  (lambda (a ctx)
    (or (aggregate-walk a ctx
                        (lambda (r key item body)
                          (declare (ignore key item))
                          (when (as-bool r (node-pos body)) (make-bool t))))
        (make-bool nil)))
  :lazy t :binds t)

(define-builtin "MAP" 2 3
  (lambda (a ctx)
    (let ((out '()))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (declare (ignore key item body))
                        (push (value-copy r) out)
                        nil))
      (make-list-value (nreverse out))))
  :lazy t :binds t)

;;; The one aggregate that preserves keys — a filtered list should still be
;;; addressable the way the original was.
(define-builtin "FILTER" 2 3
  (lambda (a ctx)
    (let ((out (make-none)))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (when (as-bool r (node-pos body))
                          (value-set out key (value-copy item)))
                        nil))
      out))
  :lazy t :binds t)

(define-builtin "SUM" 2 3
  (lambda (a ctx)
    (let ((total *dec-zero*))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (declare (ignore key item))
                        (setf total (dec-add total (as-dec r (node-pos body))))
                        nil))
      (make-num total)))
  :lazy t :binds t)

;;; Strict, not an aggregate: its second argument is a separator, not a body.
(define-builtin "JOIN" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((sep (args-text a 1))
          (at (args-pos-of a 0)))
      (%text (with-output-to-string (out)
               (loop for (key . item) in (aggregate-elements (args-val a 0))
                     for first = t then nil
                     do (progn key)
                        (unless first (write-string sep out))
                        (write-string (as-text item at) out)))))))
