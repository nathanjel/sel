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
  (:import-from #:sel-cli #:reset-probes #:say #:yn #:print-probes)
  (:export #:main))

(in-package #:sel-sqlapi)

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

;;; --- host functions with a SQL spelling (spec §8.1, sql/MAP.md §4.7) ---------
;;; The registration order, the caveat, strict mode, a LIST argument, a builder,
;;; MAP-RESET and a re-registration, through this host's own spelling of the API.

(defun host-bindings ()
  (list (cons "T" (binding-column "title" "t" :text))))

(defmacro attempt (&body body)
  "accepted, SqlError <code> for a refusal, refused for a malformed registration."
  `(handler-case (progn ,@body "accepted")
     (sql-error (e) (format nil "SqlError ~a" (sql-error-code e)))
     (error () "refused")))

(defun host-translate (source dialect &optional options)
  (translate (sel:compile-source source) dialect (host-bindings) options))

(defun local-slug (a)
  (sel:make-text (concatenate 'string "local:" (sel:args-text a 0))))

(defun host-spelling-probes ()
  (say "host.spell.before-register"
       (attempt (define-entry "postgresql" :funcs "HSLUG" (list :tpl "slug({0})" :ret "TEXT"))))
  (sel:register-function "HSLUG" 1 1 #'local-slug)
  (define-entry "postgresql" :funcs "HSLUG"
                (list :tpl "slug({0})" :ret "TEXT" :args (list "TEXT")))
  (let ((spelled (host-translate "HSLUG(T) $== \"x\"" "postgresql")))
    (say "host.spell.condition" (as-condition spelled))
    (say "host.spell.caveats"
         (if (fragment-caveats spelled) (format nil "~{~a~^,~}" (fragment-caveats spelled)) "-")))
  (say "host.spell.strict" (attempt (host-translate "HSLUG(T)" "postgresql" '(:strict t))))
  (say "host.spell.other-dialect" (attempt (host-translate "HSLUG(T)" "mariadb")))

  (sel:register-function "HHAS" 2 2 (lambda (a) (declare (ignore a)) (sel:make-bool nil)))
  (define-entry "postgresql" :funcs "HHAS"
                (list :tpl "({1} = ANY(ARRAY[{0}]))" :ret "BOOL" :args (list "LIST" "TEXT")))
  (let ((listed (host-translate "HHAS((\"a\", \"b\"), \"c\")" "postgresql")))
    (say "host.spell.list.params" (as-condition listed :params))
    (say "host.spell.list.bound" (format nil "~{~a~^,~}" (mapcar #'sel:value-dump (bindings listed)))))

  (sel:register-function "HWRAP" 1 1 (lambda (a) (sel:value-copy (sel:args-val a 0))))
  (define-builder "postgresql" :funcs "HWRAP"
                  (lambda (dialect args pos)
                    (declare (ignore pos))
                    (sel.sql::%fragment (append (list "wrap(") (fragment-parts (first args)) (list ")"))
                                        :text dialect)))
  (say "host.spell.builder" (as-value (host-translate "HWRAP(T)" "postgresql")))

  (sel:register-function "HSLUG" 1 2 #'local-slug)
  (say "host.spell.reregistered-arity" (attempt (host-translate "HSLUG(T)" "postgresql")))
  (sel:register-function "HSLUG" 1 1 #'local-slug)
  (say "host.spell.arity-restored" (attempt (host-translate "HSLUG(T)" "postgresql")))
  (map-reset)
  (say "host.spell.after-reset" (attempt (host-translate "HSLUG(T)" "postgresql")))
  (say "host.spell.after-reset.local" (sel:as-text (sel:evaluate "HSLUG(\"A\")"))))

;;; --- rendering and registration state ---------------------------------------
;;; The questions a snapshot of ONE translation cannot ask: what an unknown render
;;; mode does when there is nothing to bind, and whether a refused dialect stays
;;; refused. REFUSES collapses the host's own condition classes, which differ, to
;;; the one thing the contract says: the call did not produce SQL.

(defmacro refuses (&body body)
  `(handler-case (progn ,@body "accepted")
     (error () "refused")))

(defun render-state-probes ()
  (let* ((one (list (cons "C" (binding-column "c" "t" :num))))
         (zero (translate (sel:compile-source "C > C") "mariadb" one))
         (onelit (translate (sel:compile-source "C > 1") "mariadb" one)))
    (say "render.mode.valid.zero-slots" (refuses (as-value zero :params)))
    (say "render.mode.bogus.zero-slots" (refuses (as-value zero :bogus)))
    (say "render.mode.bogus.zero-slots.condition" (refuses (as-condition zero :bogus)))
    (say "render.mode.bogus.with-slot"
         (refuses (as-value (translate (sel:compile-source "C > \"x\"") "mariadb" one) :bogus)))
    (say "render.mode.bogus.literal-number" (refuses (as-value onelit :bogus))))
  (define-dialect "probe-badguard"
    (list :extends "postgresql" :version "16"
          :lexical (list (cons "numericGuard"
                               "CASE WHEN ({textCast:0} ~ '^.*$') THEN CAST({0} AS NUMERIC) ELSE NULL END"))))
  (let ((named (list (cons "N" (binding-column "n" "t" :text)))))
    (dolist (k '(1 2 3))
      (say (format nil "guard.reuse.~a" k)
           (refuses (translate (sel:compile-source "N + 1") "probe-badguard" named))))
    (map-reset)
    (say "guard.reuse.after-reset"
         (refuses (translate (sel:compile-source "N + 1") "probe-badguard" named)))))

(defun skel-builder-probes ()
  "A builder registered on `skel` has no template to fill: the LIMIT it would
spell is refused, so the TAKE stays in memory (sql/MAP.md §5)."
  (define-builder "mariadb" :skel "limit"
                  (lambda (dialect args pos)
                    (declare (ignore args pos))
                    (sel.sql::%fragment (list "LIMIT 1") :text dialect)))
  (let ((plan (plan-hybrid (sel:compile-source "ORDERS .> TAKE(2)") "mariadb" (probe-bindings))))
    (say "skel.builder.plan"
         (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
               ((hybrid-plan-pure-memory-p plan) "pure_memory")
               (t "hybrid")))
    (say "skel.builder.statement"
         (if (hybrid-plan-sql-statement plan) (as-statement (hybrid-plan-sql-statement plan)) "-")))
  (map-reset))

(defun main ()
  (reset-probes)
  (probe "sql" "ORDERS .> FILTER(_[\"AMOUNT\"] > 10) .> MAP(RECORD(\"id\", _[\"ID\"], \"amount\", _[\"AMOUNT\"]))")
  (probe "hybrid" "ORDERS .> SORT_BY(_[\"AMOUNT\"]) .> FILTER(_K > 1)")
  (probe "memory" "A += 1; ORDERS .> TAKE(1)")
  (fragment-probe "canon.postgresql" "postgresql" "CANON(1.50)")
  (fragment-probe "canon.mariadb" "mariadb" "CANON(1.50)")
  (fragment-probe "canon.sqlite" "sqlite" "CANON(1.50)")
  (fragment-probe "abs.postgresql" "postgresql" "ABS(1.50)")
  (host-spelling-probes)
  (render-state-probes)
  (skel-builder-probes)
  (print-probes)
  (sb-ext:exit :code 0))
