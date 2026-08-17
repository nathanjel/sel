;;;; Checks the Common Lisp decimal core against the Python oracle.
;;;;
;;;;   lisp/bin/check-decimal oracle.txt
;;;;
;;;; The decimal core is internal — nothing outside the language should be doing
;;;; arithmetic on SEL's internal representation — so this reaches into the
;;;; package with :: on purpose. It is a whitebox check, not an example of use.

(in-package #:sel-cli)

(defun split-on (char s)
  (let ((parts '())
        (start 0))
    (loop for i from 0 below (length s)
          when (char= (char s i) char)
            do (push (subseq s start i) parts)
               (setf start (1+ i)))
    (push (subseq s start) parts)
    (nreverse parts)))

(defun main ()
  (let ((path (first (script-args)))
        (cases 0)
        (mismatches 0)   ; counted in full; FAILURES is only the display
        (failures '()))
    (unless path
      (format *error-output* "usage: check-decimal oracle.txt~%")
      (sb-ext:exit :code 2))
    (dolist (line (split-lines (read-text-file path)))
      (when (plusp (length line))
        (destructuring-bind (op a b want) (split-on #\| line)
          (incf cases)
          (let* ((da (sel::dec-parse a))
                 (db (sel::dec-parse b))
                 (got (handler-case
                          (cond
                            ((string= op "+") (sel::dec-format (sel::dec-add da db)))
                            ((string= op "-") (sel::dec-format (sel::dec-sub da db)))
                            ((string= op "*") (sel::dec-format (sel::dec-mul da db)))
                            ((string= op "/") (sel::dec-format (sel::dec-div da db)))
                            ((string= op "%") (sel::dec-format (sel::dec-mod da db)))
                            ((string= op "cmp") (format nil "~d" (sel::dec-cmp da db)))
                            ((string= op "round")
                             (sel::dec-format (sel::dec-round da (sel::dec-to-int db))))
                            ((string= op "floor") (sel::dec-format (sel::dec-floor da)))
                            ((string= op "ceil") (sel::dec-format (sel::dec-ceil da)))
                            ((string= op "trunc") (sel::dec-format (sel::dec-trunc da)))
                            (t (format *error-output* "unknown op ~a~%" op)
                               (sb-ext:exit :code 2)))
                        (sel:sel-error (e)
                          (format nil "THREW ~a" (sel:sel-error-code e))))))
            (when (string/= got want)
              (incf mismatches)
              (when (< (length failures) 20)
                (push (format nil "~a ~a ~a => ~a, oracle says ~a" a op b got want) failures)))))))
    (format t "lisp: ~d cases, ~d mismatches~%" cases mismatches)
    (dolist (f (reverse failures)) (format t "  ~a~%" f))
    (when (> mismatches (length failures))
      (format t "  ... and ~d more~%" (- mismatches (length failures))))
    (sb-ext:exit :code (if (plusp mismatches) 1 0))))
