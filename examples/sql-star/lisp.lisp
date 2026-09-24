;;;; A star schema — whole pipelines in SQL, and split with memory, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-star              (starts the databases for you)
;;;;
;;;; fact_sales sits in the middle; dim_date, dim_store and dim_product around it.
;;;; The application describes each table once, as a relation binding, and then
;;;; hands SEL whole pipelines. PLAN-HYBRID decides how much of each one the
;;;; database can answer: all of it (pure_sql), a prefix of it (hybrid, the rest
;;;; runs in memory over the rows the prefix returned), or none of it
;;;; (pure_memory). The answer is checked against a run of the same program over
;;;; the tables loaded into memory.
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
  (list (cons "SALES"    (relation "fact_sales" "s" "sale_id" :num "date_key" :num
                                   "product_key" :num "store_key" :num "qty" :num
                                   "revenue" :num))
        (cons "DATES"    (relation "dim_date" "d" "date_key" :num "year" :num "quarter" :num
                                   "month" :num "month_name" :text))
        (cons "STORES"   (relation "dim_store" "t" "store_key" :num "city" :text
                                   "region" :text "format" :text))
        (cons "PRODUCTS" (relation "dim_product" "p" "product_key" :num "sku" :text
                                   "name" :text "category" :text "brand" :text
                                   "list_price" :num))))
;; EXAMPLE-END bindings

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: the pipelines sit beside it, one .sel file each.")

(defun read-file (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defparameter *pipelines*
  '(("revenue by category, first quarter"           . "revenue-by-category.sel")
    ("best-selling product per region, stores only" . "best-product-per-region.sel"))
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
    (loop for (name table key) in '(("SALES" "fact_sales" "sale_id") ("DATES" "dim_date" "date_key")
                                    ("STORES" "dim_store" "store_key")
                                    ("PRODUCTS" "dim_product" "product_key"))
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
