;;;; Translate a corpus of SEL programs and print one canonical line each.
;;;;
;;;;   lisp/bin/sqlfuzz corpus.selc [dialect]
;;;;
;;;; The counterpart of the other hosts' sqlfuzz; see js/bin/sqlfuzz.mjs for why
;;;; this lane exists. The corpus format and the one-line-per-program protocol
;;;; are specified in tools/README.md.

(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-sqlfuzz (:use #:common-lisp #:sel.sql) (:export #:main))
(in-package #:sel-sqlfuzz)

(defun read-corpus (text)
  "Records start at a `### ` line. Split on #\\Newline and nothing else: a
splitlines-style break would also cut on other separators the corpus contains
deliberately."
  ;; STARTED is a separate flag rather than a non-NIL CUR: '() IS NIL, so a
  ;; freshly-opened record is indistinguishable from "no record yet" and every
  ;; line would be dropped.
  (let ((records '()) (cur '()) (started nil))
    (dolist (line (sel-cli:split-lines text))
      (if (sel-cli:starts-with "### " line)
          (progn (when started (push (nreverse cur) records))
                 (setf cur '() started t))
          (when started (push line cur))))
    (when started (push (nreverse cur) records))
    (mapcar (lambda (lines)
              (let ((joined (format nil "~{~a~^~%~}" lines)))
                (if (and (plusp (length joined))
                         (char= (char joined (1- (length joined))) #\Newline))
                    (subseq joined 0 (1- (length joined)))
                    joined)))
            (nreverse records))))

(defun escape-newlines (s)
  (with-output-to-string (o)
    (loop for c across s do (if (char= c #\Newline) (write-string "\\n" o) (write-char c o)))))

(defun main ()
  (let* ((args (sel-cli:script-args))
         (path (first args))
         (dialect (or (second args) "mariadb"))
         (corpus (read-corpus (sel-cli:read-text-file path))))
    (dolist (src corpus)
      (write-string
       (escape-newlines
        (handler-case
            (let ((program (sel:compile-source src)))
              (handler-case
                  ;; Three renderings, because comparing only the inline one once
                  ;; let a mutation that bound a numeric literal as a parameter
                  ;; walk straight through this lane.
                  (let ((f (translate program dialect)))
                    (format nil "~a | ~a | ~{~a~^,~}"
                            (as-value f) (as-value f :params)
                            (mapcar #'sel:value-dump (bindings f))))
                (sql-error (e) (format nil "!~a@~a:~a" (sql-error-code e)
                                       (sql-error-line e) (sql-error-col e)))
                (sel:sel-error (e) (format nil "!SEL ~a@~a:~a" (sel:sel-error-code e)
                                           (sel:sel-error-line e) (sel:sel-error-col e)))
                (error (e) (format nil "!HOST ~a" (type-of e)))))
          (sel:sel-error () "-")
          (error (e) (format nil "!HOST ~a" (type-of e))))))
      (terpri))
    0))
