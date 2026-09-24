;;;; A report no database can take a share of — SQL loads, SEL computes, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-complex           (starts the databases for you)
;;;;
;;;; The support desk's SLA report (examples/lib/tickets-report.sel) digs incident
;;;; numbers out of subjects with RGROUPS and searches the event log for each
;;;; ticket's first answer. PLAN-HYBRID finds no step of it PostgreSQL can answer,
;;;; and says so: pure_memory. So the database's job shrinks to handing over the
;;;; tables — with the SELECTs written by SEL too, from the same bindings — and
;;;; the report runs in memory over what came back. The last line checks it
;;;; against the report over the data generated in memory
;;;; (examples/memory-complex), which is where the rows in this database came
;;;; from.
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

(defparameter *lib* (merge-pathnames "../lib/" *load-truename*)
  "examples/lib/, where the support desk's SEL programs live.")

(defun read-lib (name)
  (uiop:read-file-string (merge-pathnames name *lib*) :external-format :utf-8))

(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

;; EXAMPLE-BEGIN load
(defparameter *schema*
  (list (cons "TEAMS"     (relation "teams" "g" "team_id" :num "team" :text))
        (cons "CUSTOMERS" (relation "customers" "c" "customer_id" :num
                                    "customer" :text "plan" :text))
        (cons "SLA"       (relation "sla" "s" "plan" :text "priority" :text
                                    "respond_within" :num "resolve_within" :num))
        (cons "TICKETS"   (relation "tickets" "t" "ticket_id" :num "customer_id" :num
                                    "team_id" :num "priority" :text "subject" :text
                                    "opened_at" :num "closed_at" :num))
        (cons "EVENTS"    (relation "events" "e" "event_id" :num "ticket_id" :num
                                    "seq" :num "at" :num "kind" :text "actor" :text))))

(defparameter *keys*
  '(("TEAMS" . "team_id") ("CUSTOMERS" . "customer_id") ("SLA" . "plan")
    ("TICKETS" . "ticket_id") ("EVENTS" . "event_id")))

(defun report-from-database ()
  "Plan the report, load what it reads from PostgreSQL, and run it in memory.
Returns the result and the compiled report."
  (let* ((report (sel:compile-source (read-lib "tickets-report.sel")))
         (plan (sel.sql:plan-hybrid report "postgresql" *schema*)))
    (format t "1. the report, planned for PostgreSQL~%")
    (format t "   plan        ~a~%"
            (if (sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory" "pushed down"))
    (format t "   reads       ~{~a~^, ~}~%" (sel.sql:hybrid-plan-source-tables plan))

    (format t "2. so SQL only loads the tables it reads~%")
    (let ((conn (sel-db:connect "postgresql"))
          (tables (sel:make-none)))
      (dolist (name (sel:dependencies report))
        (let* ((loader (sel:compile-source
                        (format nil "~a .> SORT_BY(_[\"~a\"])"
                                name (cdr (assoc name *keys* :test #'string=)))))
               (sql (sel.sql:as-statement
                     (sel.sql:translate-statement loader "postgresql" *schema*))))
          (sel:value-set tables name (sel-db:query conn sql))
          (format t "   ~10a  ~3D rows  ~a~%"
                  name (sel:value-size (sel:value-get tables name)) sql)))

      (format t "3. and SEL computes the report over them~%")
      (let ((result (sel:run report tables)))
        (format t "~a~%" (sel-db:render result "   | "))
        (values result report)))))
;; EXAMPLE-END load

(defun main ()
  (multiple-value-bind (result report) (report-from-database)
    (let ((generated (sel:evaluate (read-lib "tickets-generate.sel"))))
      (format t "   over the generated rows: ~a~%"
              (if (string= (sel:value-dump (sel:run report generated)) (sel:value-dump result))
                  "same report"
                  "DIFFERENT"))))
  0)
