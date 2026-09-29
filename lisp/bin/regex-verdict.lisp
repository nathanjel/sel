;;;; Verdict driver for tools/check-regex-ambiguity-diff.py: reads one pattern per
;;;; line on stdin and prints `A` when this host's regex validator accepts it and
;;;; `R` when it refuses it with E_REGEX_SYNTAX. An optional argument `i` checks
;;;; under the i flag.
;;;;
;;;;   lisp/bin/regex-verdict [i] < patterns.txt

(in-package #:sel-cli)

(defun main ()
  (let ((ic (member "i" (script-args) :test #'string=)))
    (loop for line = (read-line *standard-input* nil nil)
          while line
          do (write-line
              (handler-case
                  (progn (sel::check-regex-pattern line (and ic t) nil nil) "A")
                (sel:sel-error (e)
                  (if (string= (sel:sel-error-code e) "E_REGEX_SYNTAX")
                      "R"
                      (format nil "E:~a" (sel:sel-error-code e)))))))
    (finish-output)
    (sb-ext:exit :code 0)))
