;;;; A third-normal-form shop — joins, grouping and a split, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-3nf               (starts the databases for you)
;;;;
;;;; categories, products, customers, orders and order_lines, each fact stored
;;;; once. The first pipeline is SQL from end to end. The second assigns every
;;;; customer to an A/B cohort by CRC32 of their e-mail — the application's own
;;;; hashing, which PostgreSQL has no spelling for — so the database joins,
;;;; filters and multiplies, and the cohorts are computed in memory over what it
;;;; returned.
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
  (list (cons "CUSTOMERS" (relation "customers" "c" "customer_id" :num "name" :text
                                    "email" :text "country" :text))
        (cons "ORDERS"    (relation "orders" "o" "order_id" :num "customer_id" :num
                                    "status" :text "ordered_on" :text))
        (cons "LINES"     (relation "order_lines" "l" "order_id" :num "line_no" :num
                                    "product_id" :num "qty" :num "unit_price" :num))
        (cons "PRODUCTS"  (relation "products" "p" "product_id" :num "sku" :text
                                    "title" :text "category_id" :num "list_price" :num))))
;; EXAMPLE-END bindings

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: the pipelines sit beside it, one .sel file each.")

(defun read-file (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defparameter *pipelines*
  '(("paid revenue per product since March" . "revenue-per-product.sel")
    ("paid revenue per experiment cohort"   . "revenue-per-cohort.sel"))
  "(title . file) per pipeline, in the order they run.")

;; EXAMPLE-BEGIN run
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in PostgreSQL as PostgreSQL can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "postgresql" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
;; EXAMPLE-END run

(defun main ()
  (let ((conn (sel-db:connect "postgresql"))
        (tables (sel:make-none)))
    ;; The same tables in memory, for the comparison at the end of each pipeline.
    (loop for (name table key) in '(("CUSTOMERS" "customers" "customer_id")
                                    ("ORDERS" "orders" "order_id")
                                    ("LINES" "order_lines" "order_id, line_no")
                                    ("PRODUCTS" "products" "product_id"))
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
