;;;; Goes in the matching lisp/src/builtins/*.lisp, and the file must be added to
;;;; :components in lisp/sel-lang.asd. Not a runnable file.
;;;; EXAMPLE-BEGIN
(define-builtin "ORD_SUFFIX" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((n (args-non-neg-int a 0))
           (tens (mod n 100)))
      (%text (format nil "~d~a" n
                     (if (<= 11 tens 13)
                         "th"
                         (case (mod n 10) (1 "st") (2 "nd") (3 "rd") (t "th"))))))))
;;;; EXAMPLE-END
