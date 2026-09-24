;;;; An entity-attribute-value catalogue — pipelines over EAV rows, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-eav               (starts the databases for you)
;;;;
;;;; entities holds one row per product; attributes holds one (entity, name,
;;;; value) row per property, every value TEXT, whatever it means. That shape is
;;;; flexible to write and awkward to ask: "red or blue, and made of steel" is two
;;;; EXISTS subqueries, and a price is a number only when the text says so. SEL
;;;; pushes down what SQLite can answer exactly (text equality, joins, grouping)
;;;; and keeps the rest — pivoting attributes into records, comparing text as a
;;;; number — in memory, where ISNUM can say what SQLite cannot.
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
  (list (cons "PRODUCTS" (relation "entities" "e" "id" :num "sku" :text "kind" :text))
        (cons "ATTRS"    (relation "attributes" "a" "entity_id" :num "name" :text
                                   "value" :text))))
;; EXAMPLE-END bindings

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: the pipelines sit beside it, one .sel file each.")

(defun read-file (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defparameter *pipelines*
  '(("red or blue, and steel"      . "red-or-blue-steel.sel")
    ("products per colour"         . "products-per-colour.sel")
    ("priced under 60.00, pivoted" . "priced-under-60.sel"))
  "(title . file) per pipeline, in the order they run.")

;; EXAMPLE-BEGIN run
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in SQLite as SQLite can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "sqlite" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
;; EXAMPLE-END run

(defun main ()
  (let ((conn (sel-db:connect "sqlite"))
        (tables (sel:make-none)))
    ;; The same tables in memory, for the comparison at the end of each pipeline.
    (loop for (name table key) in '(("PRODUCTS" "entities" "id")
                                    ("ATTRS" "attributes" "entity_id, name"))
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
