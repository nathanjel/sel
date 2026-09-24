;;;; An unnormalised export — one wide table, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-flat              (starts the databases for you)
;;;;
;;;; order_export repeats the customer and the product on every line, the way a
;;;; spreadsheet or a nightly dump does, with the inconsistencies that come with
;;;; it: the same person under two spellings of their name and e-mail. The first
;;;; pipeline groups in SQL. The second normalises e-mails and counts distinct
;;;; customers, and the planner keeps the normalised values out of MariaDB's
;;;; hands: its collation would decide which of them are "the same", and SEL's
;;;; identity is exact bytes. The third explodes a `;`-separated column, which no
;;;; SQL step can express, so it runs in memory entirely.
;;;;
;;;; The four files beside this one print byte-identical output.

;;; Before the DEFPACKAGE, because --load reads and evaluates one top-level form
;;; at a time: the SEL.SQL and SEL-DB symbols further down are only readable once
;;; the packages they name exist.
(let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(load (merge-pathnames "../lib/db.lisp" *load-truename*))

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

;; EXAMPLE-BEGIN bindings
(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "EXPORT" (relation "order_export" "x" "line_id" :num "order_no" :text
                                 "order_date" :text "customer_name" :text
                                 "customer_email" :text "customer_city" :text
                                 "sku" :text "product_name" :text "category" :text
                                 "qty" :num "unit_price" :num "tags" :text))))
;; EXAMPLE-END bindings

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: the pipelines sit beside it, one .sel file each.")

(defun read-file (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defparameter *pipelines*
  '(("revenue per city, February and March"              . "revenue-per-city.sel")
    ("distinct customers per city, by normalised e-mail" . "customers-per-city.sel")
    ("lines per tag"                                     . "lines-per-tag.sel"))
  "(title . file) per pipeline, in the order they run.")

;; EXAMPLE-BEGIN run
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in MariaDB as MariaDB can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "mariadb" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
;; EXAMPLE-END run

(defun main ()
  (let ((conn (sel-db:connect "mariadb"))
        (tables (sel:make-none)))
    ;; The same tables in memory, for the comparison at the end of each pipeline.
    (loop for (name table key) in '(("EXPORT" "order_export" "line_id"))
          do (sel:value-set tables name
                            (sel-db:query conn (format nil "SELECT * FROM ~a ORDER BY ~a" table key))))

    (loop for (title . file) in *pipelines*
          for n from 1
          do (multiple-value-bind (rows plan program) (run-pipeline file conn tables)
               (format t "~D. ~a~%" n title)
               (format t "   plan        ~a~%"
                       (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                             ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                             (t "hybrid")))
               (format t "   reads       ~{~a~^, ~}~%" (sel.sql:hybrid-plan-source-tables plan))
               (when (sel.sql:hybrid-plan-sql-statement plan)
                 (format t "   sql         ~a~%"
                         (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))))
               (format t "~a~%" (sel-db:render rows "   | "))
               (format t "   in memory   ~a~%"
                       (if (string= (sel:value-dump (sel:run program (sel:value-copy tables)))
                                    (sel:value-dump rows))
                           "same rows"
                           "DIFFERENT")))))
  0)
