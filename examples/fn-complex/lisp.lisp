;;;; Goes in the matching lisp/src/builtins/*.lisp. Not a runnable file: this is a fragment that
;;;; compiles only in place. See README.md beside it.
;;;; EXAMPLE-BEGIN
(define-builtin "FIRST" 2 3
  (lambda (a ctx)
    (let* ((three (= (args-count a) 3))
           (binder (if three (args-symbol a 1) "_"))
           (body (args-node a (if three 2 1)))
           (list (args-val a 0))
           (items (cond ((plusp (value-size list)) (value-entries list))
                        ((eq (value-kind list) :none) '())
                        (t (list (cons "1" list))))))
      (loop for (key . item) in items
            do (ctx-push-frame ctx (list (cons binder item)
                                         (cons "_K" (make-text key))))
               (let ((hit (unwind-protect
                               (as-bool (args-eval a body) (node-pos body))
                            (ctx-pop-frame ctx))))
                 (when hit (return (value-copy item))))
            finally (return (make-text "")))))
  :lazy t :binds t)
;;;; EXAMPLE-END
