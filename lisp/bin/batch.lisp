;;;; Runs a corpus of SEL programs and prints one canonical line each, so every
;;;; implementation's output can be compared with a plain diff. Both the corpus
;;;; format and the line format are specified in tools/README.md.
;;;;
;;;;   lisp/bin/batch [--show] corpus.selc

(in-package #:sel-cli)

;;; A line beginning `### ` starts a record; everything after it is source until
;;; the next marker.
(defun strip-final-newline (s)
  "Drop the one trailing newline the file's final newline contributed. Without
this the last record of every corpus keeps a newline the other three readers
strip, which moves end-of-input error positions and shows up as a fake
disagreement in the fuzzer."
  (let ((n (length s)))
    (if (and (plusp n) (char= (char s (1- n)) #\Newline))
        (subseq s 0 (1- n))
        s)))

(defun read-corpus (text)
  (let ((records '())
        (current nil)
        (started nil))
    (dolist (line (split-lines text))
      (if (starts-with "### " line)
          (progn
            (when started (push (strip-final-newline (join-lines current)) records))
            (setf current '() started t))
          (when started (push line current))))
    (when started (push (strip-final-newline (join-lines current)) records))
    (nreverse records)))

;;; The rendering bin/sel uses, so a documentation example can be pasted into the
;;; CLI and produce exactly what the documentation claims.
(defun render (v)
  (if (zerop (sel:value-size v))
      (case (sel:value-kind v)
        (:text (sel::value-scalar v))
        (:bool (sel:value-dump v))
        (:bin (concatenate 'string "bin:" (subseq (sel:value-dump v) 1)))
        (t (sel:value-dump v)))
      (sel:value-dump v)))

(defun escape-newlines (s)
  (with-output-to-string (out)
    (loop for c across s
          do (if (char= c #\Newline) (write-string "\\n" out) (write-char c out)))))

(defun main ()
  (let* ((args (script-args))
         (show (member "--show" args :test #'string=))
         (path (find-if-not (lambda (a) (string= a "--show")) args)))
    (unless path
      (format *error-output* "usage: batch [--show] corpus.selc~%")
      (sb-ext:exit :code 2))
    (let ((lines '()))
      (dolist (src (read-corpus (read-text-file path)))
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
      ;; One line per program is the protocol; a value containing a newline must
      ;; not be allowed to desynchronise the comparison.
      (format t "~{~a~%~}" (mapcar #'escape-newlines (nreverse lines))))
    (sb-ext:exit :code 0)))
