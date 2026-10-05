;;;; Runs a corpus of SEL programs and prints one canonical line each, so every
;;;; implementation's output can be compared with a plain diff. Both the corpus
;;;; format and the line format are specified in tools/README.md.
;;;;
;;;;   lisp/bin/batch [--show] corpus.selc

(in-package #:sel-cli)

(defun main ()
  (let* ((args (script-args))
         (show (member "--show" args :test #'string=))
         (path (find-if-not (lambda (a) (string= a "--show")) args)))
    (unless path
      (format *error-output* "usage: batch [--show] corpus.selc~%")
      (sb-ext:exit :code 2))
    (let ((lines '())
          (corpus (read-corpus (read-file-or-exit path))))
      (unless corpus (no-cases "no program ran: ~a holds no `### ` record" path))
      (dolist (src corpus)
        (push
         (handler-case
             (let ((v (sel:run (sel:compile-source src) (sel:make-none))))
               (if show (render v) (sel:value-dump v)))
           (sel:sel-error (e)
             (if show
                 (format nil "!~a" (sel:sel-error-code e))
                 (format nil "!~a@~d:~d" (sel:sel-error-code e)
                         (sel:sel-error-line e) (sel:sel-error-col e))))
           ;; Anything that is not a sel-error is a bug in this implementation,
           ;; and the fuzzer reports it as such rather than as a disagreement.
           (error (e) (format nil "!HOST ~a: ~a" (type-of e) e)))
         lines))
      (format t "~{~a~%~}" (mapcar #'escape-newlines (nreverse lines))))
    (sb-ext:exit :code 0)))
