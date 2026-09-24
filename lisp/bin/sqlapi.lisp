;;;; SQL API parity probe: the planner's contract through every host's own SQL
;;;; binding. tools/check-sqlapi.sh diffs the reports of the hosts that carry the
;;;; SQL layer (the JS bundles do not; they print nothing and are left out).
;;;; Three programs -- one the planner pushes down whole, one it splits into a SQL
;;;; prefix and an in-memory continuation, one it keeps in memory -- and the same
;;;; nine questions about each plan: its classification, dialect, statement, prefix
;;;; and continuation presence, the continuation's dependencies, its source
;;;; variable, the physical source tables and the selected member. The probe NAMES
;;;; are the contract and the VALUES are compared; each host spells its accessors
;;;; its own way (SEL-0044).

(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-sqlapi
  (:use #:common-lisp #:sel.sql)
  (:export #:main))

(in-package #:sel-sqlapi)

(defvar *probes* '())
(defvar *probe-n* 0)

(defun say (name value)
  (push (format nil "~2,'0d ~a = ~a" (incf *probe-n*) name value) *probes*))

(defun yn (x) (if x "true" "false"))

(defun probe-bindings ()
  (list (cons "ORDERS" (binding-relation "orders" "o"
                         (list (cons "ID" (binding-column "id" "o" :num))
                               (cons "CUSTOMER_ID" (binding-column "customer_id" "o" :num))
                               (cons "AMOUNT" (binding-column "amount" "o" :num))
                               (cons "NAME" (binding-column "name" "o" :text)))))
        (cons "CUSTOMERS" (binding-relation "customers" "c"
                            (list (cons "ID" (binding-column "id" "c" :num))
                                  (cons "NAME" (binding-column "name" "c" :text)))))))

(defun probe (label source)
  (let ((plan (plan-hybrid (sel:compile-source source) "mariadb" (probe-bindings))))
    (say (format nil "plan.~a.kind" label)
         (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
               ((hybrid-plan-pure-memory-p plan) "pure_memory")
               (t "hybrid")))
    (say (format nil "plan.~a.dialect" label) (or (hybrid-plan-dialect plan) "-"))
    (say (format nil "plan.~a.statement" label)
         (if (hybrid-plan-sql-statement plan) (as-statement (hybrid-plan-sql-statement plan)) "-"))
    (say (format nil "plan.~a.prefix.present" label) (yn (hybrid-plan-sql-prefix-ast plan)))
    (say (format nil "plan.~a.continuation.present" label) (yn (hybrid-plan-continuation-ast plan)))
    (say (format nil "plan.~a.continuation.deps" label)
         (if (hybrid-plan-continuation-program plan)
             (format nil "~{~a~^ ~}" (sel:dependencies (hybrid-plan-continuation-program plan)))
             "-"))
    (say (format nil "plan.~a.source.var" label) (hybrid-plan-continuation-source-var plan))
    (say (format nil "plan.~a.tables" label) (format nil "~{~a~^,~}" (hybrid-plan-source-tables plan)))
    (say (format nil "plan.~a.selected.member" label) (if (hybrid-plan-selected-member plan) "present" "-"))))

;;; The canonical flag is public: an application (and the SQL oracle) reads it
;;; to know the fragment promised a spelling, not only a value (SEL-0058).
(defun fragment-probe (label dialect source)
  (let ((f (translate (sel:compile-source source) dialect (probe-bindings))))
    (say (format nil "fragment.~a.kind" label) (string-upcase (string (fragment-kind f))))
    (say (format nil "fragment.~a.canonical" label) (yn (fragment-canonical f)))
    (say (format nil "fragment.~a.caveats" label)
         (if (fragment-caveats f) (format nil "~{~a~^,~}" (fragment-caveats f)) "-"))))

(defun main ()
  (setf *probes* '() *probe-n* 0)
  (probe "sql" "ORDERS .> FILTER(_[\"AMOUNT\"] > 10) .> MAP(RECORD(\"id\", _[\"ID\"], \"amount\", _[\"AMOUNT\"]))")
  (probe "hybrid" "ORDERS .> SORT_BY(_[\"AMOUNT\"]) .> FILTER(_K > 1)")
  (probe "memory" "A += 1; ORDERS .> TAKE(1)")
  (fragment-probe "canon.postgresql" "postgresql" "CANON(1.50)")
  (fragment-probe "canon.mariadb" "mariadb" "CANON(1.50)")
  (fragment-probe "canon.sqlite" "sqlite" "CANON(1.50)")
  (fragment-probe "abs.postgresql" "postgresql" "ABS(1.50)")
  (format t "~{~a~%~}" (reverse *probes*))
  (sb-ext:exit :code 0))
