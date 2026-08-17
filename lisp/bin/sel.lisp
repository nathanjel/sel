;;;; SEL command line: evaluate an expression, a file, or start a REPL.
;;;;
;;;;   sel -e 'EXPR'          evaluate and print
;;;;   sel file.sel           evaluate a file
;;;;   sel --deps -e 'EXPR'   print the variables the expression reads
;;;;   sel --functions        list the function table
;;;;   sel                    REPL, keeping one context across lines

(in-package #:sel-cli)

(defun show (v)
  (if (zerop (sel:value-size v))
      (case (sel:value-kind v)
        (:text (sel::value-scalar v))
        (:bool (sel:value-dump v))
        (:bin (concatenate 'string "bin:" (subseq (sel:value-dump v) 1)))
        (t (sel:value-dump v)))
      (sel:value-dump v)))

(defun report (e)
  (format *error-output* "~a at line ~d column ~d: ~a~%"
          (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)
          (sel:sel-error-message e)))

(defun main ()
  (let* ((all (script-args))
         (want-deps (member "--deps" all :test #'string=))
         (args (remove "--deps" all :test #'string=)))

    (when (string= (or (first args) "") "--functions")
      (format t "~{~a~%~}" (sel:function-names))
      (sb-ext:exit :code 0))

    (let ((source (cond ((and (string= (or (first args) "") "-e") (second args))
                         (second args))
                        ((first args) (read-text-file (first args)))
                        (t nil))))
      (if source
          (handler-case
              (let ((program (sel:compile-source source)))
                (if want-deps
                    (format t "~{~a~%~}" (sel:dependencies program))
                    (format t "~a~%" (show (sel:run program)))))
            (sel:sel-error (e) (report e) (sb-ext:exit :code 1)))
          ;; REPL: one context for the whole session, so assignments persist.
          (let ((root (sel:make-none)))
            (loop
              (format t "sel> ")
              (finish-output)
              (let ((line (read-line *standard-input* nil nil)))
                (when (null line) (format t "~%") (return))
                (when (plusp (length (trim-ws line)))
                  (handler-case
                      (format t "~a~%" (show (sel:run (sel:compile-source line) root)))
                    (sel:sel-error (e) (report e)))))))))
    (sb-ext:exit :code 0)))
