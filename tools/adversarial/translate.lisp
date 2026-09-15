(require :asdf)
(push #p"/work/lisp/" asdf:*central-registry*)
(let ((*standard-output* (make-broadcast-stream))) (asdf:load-system :sel-lang/sql))
(defun jq (s) (subseq (sel:value-dump (sel:make-text s)) 1))
(defvar *replay* (when (uiop:getenv "AUDIT_REPLAY") (open "/work/tools/adversarial/replay-lisp.sel")))
(defun audit-bindings ()
 (loop for tab in '("r" "s") collect
  (cons (string-upcase tab) (sel.sql:binding-relation tab tab
   (loop for col in '("id" "cat" "v" "fk") collect
    (cons (string-upcase col) (sel.sql:binding-column col tab (if (equal col "cat") :text :num))))))))
(with-open-file (in "/work/tools/adversarial/queries.sel")
 (loop for source = (read-line in nil) while source for i from 0 do
  (let ((p (sel:compile-source source)) (b (audit-bindings)))
   (dolist (d '("sqlite" "postgresql" "mariadb")) (dolist (strict '(nil t))
    (format t "{\"i\":~d,\"d\":~a,\"strict\":~a" i (jq d) (if strict "true" "false"))
    (handler-case
     (let* ((f (sel.sql:translate-statement p d b (list :strict strict))) (sql (sel.sql:as-statement f)) (ps (sel.sql:as-statement f :params)))
      (format t ",\"sql\":~a,\"params_sql\":~a,\"params\":[~{~a~^,~}]" (jq sql) (jq ps) (mapcar (lambda(v)(jq(sel:as-text v))) (sel.sql:bindings f))))
     (sel.sql:sql-error(e)(format t ",\"error\":~a" (jq(sel.sql:sql-error-code e))))
     (error(e)(format t ",\"error\":~a" (jq(princ-to-string e)))))
    (handler-case
     (let ((h (sel.sql:plan-hybrid p d b (list :strict strict))))
      (format t ",\"plan\":~a,\"prefix\":~a" (jq(cond((sel.sql:hybrid-plan-pure-sql-p h)"pure_sql")((sel.sql:hybrid-plan-pure-memory-p h)"pure_memory")(t "hybrid")))
       (if(sel.sql:hybrid-plan-sql-statement h)(jq(sel.sql:as-statement(sel.sql:hybrid-plan-sql-statement h)))"null")))
     (sel.sql:sql-error(e)(format t ",\"plan_error\":~a" (jq(sel.sql:sql-error-code e)))))
    (when *replay*
     (let ((data (sel:run(sel:compile-source(read-line *replay*)))))
      (handler-case
       (progn
        (unless (equal "" (sel:as-text(sel:value-get data "error"))) (error "DB_ERROR"))
        (let* ((h(sel.sql:plan-hybrid p d b(list :strict strict)))
               (v(sel.sql:execute-hybrid h (lambda(sql params)(declare(ignore sql params))(sel:value-get data "rows")) (sel:value-get data "context"))))
         (format t ",\"hybrid_value\":~a" (jq(sel:value-dump v)))))
       (sel:sel-error(e)(format t ",\"hybrid_error\":~a" (jq(sel:sel-error-code e))))
       (error(e)(format t ",\"hybrid_error\":~a" (jq(princ-to-string e)))))))
    (format t "}~%"))))))
