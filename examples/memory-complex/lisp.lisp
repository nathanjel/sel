;;;; The same report with no database at all — generated data, in memory, from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/memory-complex/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; examples/sql-complex loads the support desk from PostgreSQL. Here the same
;;;; rows come from examples/lib/tickets-generate.sel — a SEL program that builds
;;;; them deterministically, and the source the PostgreSQL seed was rendered from
;;;; — and the same report runs over them. Nothing below opens a connection: the
;;;; plan for MariaDB is computed from the bindings alone, and it says what it
;;;; said for PostgreSQL, that none of this report is SQL's to answer.
;;;;
;;;; The four files beside this one print byte-identical output.

;;; Before the DEFPACKAGE, because --load reads and evaluates one top-level form
;;; at a time: the SEL.SQL and SEL-DB symbols further down are only readable once
;;; the packages they name exist. render.lisp, not db.lisp: nothing here needs a
;;; database driver.
(let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(load (merge-pathnames "../lib/render.lisp" *load-truename*))

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

;; EXAMPLE-BEGIN generate
(defun report-in-memory ()
  "Generate the support desk in memory, report on it, and return the report."
  (let ((data (sel:evaluate (read-lib "tickets-generate.sel")))
        (report (sel:compile-source (read-lib "tickets-report.sel"))))
    (format t "1. generated in memory~%")
    (dolist (name (sel:value-keys data))
      (format t "   ~10a  ~3D rows~%" name (sel:value-size (sel:value-get data name))))
    (format t "2. the report~%")
    (format t "~a~%" (sel-db:render (sel:run report data) "   | "))
    report))
;; EXAMPLE-END generate

(defun main ()
  (let* ((report (report-in-memory))
         ;; Planning needs the schema, not a server: the bindings sql-complex
         ;; describes PostgreSQL with, asked about MariaDB this time.
         (schema
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
         (plan (sel.sql:plan-hybrid report "mariadb" schema)))
    (format t "3. planned for MariaDB, without connecting~%")
    (format t "   plan        ~a~%"
            (if (sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory" "pushed down"))
    (format t "   reads       ~{~a~^, ~}~%" (sel.sql:hybrid-plan-source-tables plan)))
  0)
