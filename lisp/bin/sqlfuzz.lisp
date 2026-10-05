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

;; The relations the corpus's pipelines read (tools/gen-programs.mjs --sql), the
;; same in every host's runner: two tables, a NUM join key, a TEXT field whose
;; name both sides share.
(defun fuzz-bindings ()
  (list (cons "ORDERS" (binding-relation "orders" "o"
                         (list (cons "ID" (binding-column "id" "o" :num))
                               (cons "CUSTOMER_ID" (binding-column "customer_id" "o" :num))
                               (cons "AMOUNT" (binding-column "amount" nil :num))
                               (cons "NAME" (binding-column "name" "o" :text)))))
        (cons "CUSTOMERS" (binding-relation "customers" "c"
                            (list (cons "ID" (binding-column "id" "c" :num))
                                  (cons "NAME" (binding-column "name" "c" :text)))))))

(defun render (f)
  (format nil "~a | ~a | ~{~a~^,~}" (as-value f) (as-value f :params)
          (mapcar #'sel:value-dump (bindings f))))

;; Three lanes per program, `||`-separated: translate, translate-statement and
;; plan-hybrid (its classification, then its statement in params mode).
(defun attempt (thunk)
  (handler-case (funcall thunk)
    (sql-error (e) (format nil "!~a@~a:~a" (sql-error-code e) (sql-error-line e) (sql-error-col e)))
    (sel:sel-error (e) (format nil "!SEL ~a@~a:~a" (sel:sel-error-code e)
                               (sel:sel-error-line e) (sel:sel-error-col e)))
    (error (e) (format nil "!HOST ~a" (type-of e)))))

(defun main ()
  (let* ((args (sel-cli:script-args))
         (path (or (first args)
                   (progn (format *error-output* "usage: sqlfuzz corpus.selc [dialect] [all|statement]~%")
                          (sb-ext:exit :code 2))))
         (dialect (or (second args) "mariadb"))
         ;; `statement`: only translate-statement's inline SQL; see js/bin/sqlfuzz.mjs.
         (mode (or (third args) "all"))
         (corpus (sel-cli:read-corpus (sel-cli:read-file-or-exit path))))
    (unless corpus (sel-cli:no-cases "no program ran: ~a holds no `### ` record" path))
    (dolist (src corpus)
      (write-string
       (sel-cli:escape-newlines
        (handler-case
            (let ((program (sel:compile-source src))
                  (bindings (fuzz-bindings)))
              ;; Three renderings, because comparing only the inline one once
              ;; let a mutation that bound a numeric literal as a parameter
              ;; walk straight through this lane.
              (if (string= mode "statement")
                  (attempt (lambda () (as-statement (translate-statement program dialect bindings))))
                  (format nil "~a || ~a || ~a"
                          (attempt (lambda () (render (translate program dialect bindings))))
                          (attempt (lambda () (render (translate-statement program dialect bindings))))
                          (attempt (lambda ()
                                     (let* ((plan (plan-hybrid program dialect bindings))
                                            (kind (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
                                                        ((hybrid-plan-pure-memory-p plan) "pure_memory")
                                                        (t "hybrid")))
                                            (frag (hybrid-plan-sql-statement plan)))
                                       (if frag (format nil "~a ~a" kind (as-statement frag :params)) kind)))))))
          (sel:sel-error () "-")
          (error (e) (format nil "!HOST ~a" (type-of e))))))
      (terpri))
    0))
