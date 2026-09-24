;;;; A read-eval-print loop — the whole of it, in Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/repl/lisp.lisp \
;;;;        --eval '(sel-example:main)' < examples/repl/session.txt
;;;;
;;;; One context lives across lines, so a variable assigned on one line is there
;;;; on the next. Two commands besides SEL itself: `:deps <expr>` lists what an
;;;; expression reads, and `:reset` empties the context. Errors print their code
;;;; and position — the message is human text and may differ between hosts; the
;;;; code and the position may not.
;;;;
;;;; The four files beside this one print byte-identical output for the session
;;;; in session.txt.

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

;; EXAMPLE-BEGIN repl
(defun show (value)
  (cond ((sel:value-bool-p value) (if (sel:as-bool value) "TRUE" "FALSE"))
        ((sel:value-null-p value) "NULL")
        ((or (plusp (sel:value-size value)) (sel:value-bin-p value)) (sel:value-dump value))
        (t (sel:as-text value))))

(defun blank-p (line)
  (every (lambda (c) (member c '(#\Space #\Tab #\Return #\Page))) line))

(defun main ()
  (let ((context (sel:make-none)))
    (loop for line = (read-line *standard-input* nil)
          while line
          unless (blank-p line)
            do (format t "sel> ~a~%" line)
               (handler-case
                   (cond ((string= line ":reset")
                          (setf context (sel:make-none)))
                         ((and (>= (length line) 6) (string= ":deps " line :end2 6))
                          (format t "~{~a~^ ~}~%"
                                  (sel:dependencies (sel:compile-source (subseq line 6)))))
                         (t
                          (format t "~a~%" (show (sel:run (sel:compile-source line) context)))))
                 (sel:sel-error (e)
                   (format t "~a at ~D:~D~%"
                           (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
  0)
;; EXAMPLE-END repl
