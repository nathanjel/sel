;;;; SQL-aimed usage — pushing a rule down to the database, from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/sql/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; The same rule that validates one order in the application can filter a
;;;; million of them in the database. What makes that safe is that the
;;;; translation refuses rather than guesses: if SQL cannot be made to mean what
;;;; SEL means, no SQL is emitted and the rule stays where it already worked.
;;;;
;;;; The four files beside this one print byte-identical output;
;;;; tools/check-examples.sh diffs them.
;;;;
;;;; The visible difference here is the load below. boot.lisp brings in
;;;; :SEL-LANG, the evaluator alone; the translator is the separate system
;;;; :SEL-LANG/SQL, for the reason the JS host puts it behind its own entry
;;;; point — a program that only evaluates should not carry the dialect map.

;;; Before the DEFPACKAGE, because --load reads and evaluates one top-level form
;;; at a time: the SEL.SQL symbols further down are only readable once the
;;; package they name exists.
(let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

(defun main ()
  ;; 1 — a rule, and what the database should call its inputs ------------------
  ;; DEPENDENCIES says exactly what has to be bound. A name the program reads
  ;; and the bindings do not describe is a refusal, not a guess.

  (format t "1. a rule pushed down~%")
  (let* ((rule (sel:compile-source "TOTAL > 100.00 AND STATUS $== \"open\""))
         ;; The bindings are an alist rather than a hash table: they are given
         ;; in an order and read back in one, and a literal alist says that.
         (bindings (list (cons "TOTAL"  (sel.sql:binding-column "total" "o" :num))
                         (cons "STATUS" (sel.sql:binding-column "status" "o" :text))))
         (frag (sel.sql:translate rule "mariadb" bindings)))
    (format t "   needs        => ~{~a~^ ~}~%" (sel:dependencies rule))
    (format t "   sql          => ~a~%" (sel.sql:as-condition frag))

    ;; 2 — the same rule as a prepared statement --------------------------------
    ;; Inline is for reading and for a query you build once. :PARAMS is what you
    ;; hand a driver: the literals become placeholders and BINDINGS gives the
    ;; values in the order the placeholders appear in the output.

    (format t "2. as parameters~%")
    (format t "   sql          => ~a~%" (sel.sql:as-condition frag :params))
    (format t "   values       => ~{~a~^, ~}~%"
            (mapcar #'sel:value-dump (sel.sql:bindings frag)))

    ;; 3 — one rule, every dialect ------------------------------------------------
    ;; The differences below are the databases', not the rule's. Nothing in the
    ;; program changed.

    (format t "3. every dialect~%")
    (dolist (dialect (sel.sql:dialects))
      (format t "   ~12a => ~a~%" dialect
              (sel.sql:as-condition (sel.sql:translate rule dialect bindings))))

    ;; 4 — a rule over a related table ---------------------------------------------
    ;; An aggregate over a relation becomes EXISTS / NOT EXISTS with a
    ;; correlation, which is the shape a database can actually use an index for.

    (format t "4. over a relation~%")
    (let ((lines (sel:compile-source "ALL(ITEMS, I, I[\"QTY\"] > 0)")))
      (format t "   sql          => ~a~%"
              (sel.sql:as-condition
               (sel.sql:translate
                lines "mariadb"
                (list (cons "ITEMS"
                            (sel.sql:binding-relation
                             "order_items" "oi"
                             (list (cons "QTY" (sel.sql:binding-column "qty" nil :num)))
                             nil "`oi`.`order_id` = `o`.`id`")))))))

    ;; 5 — refusal is an ordinary answer ---------------------------------------------
    ;; TRY-TRANSLATE returns NIL so the caller can fall back to the evaluator
    ;; without a HANDLER-CASE. TRANSLATE signals the same refusal with the reason
    ;; written out, which is what you want in a build-time audit of a rule set.

    (format t "5. refusal~%")
    (let ((unbound (sel:compile-source "MYSTERY > 1")))
      ;; The word printed is `null`, not NIL: this file's output has to be
      ;; byte-identical to its four siblings, so the answer is spelled the same
      ;; way on every host rather than in each one's own vocabulary.
      (format t "   tryTranslate => ~a~%"
              (if (null (sel.sql:try-translate unbound "mariadb" bindings))
                  "null — evaluate it in the host instead"
                  "translated"))
      ;; Only SQL-ERROR is caught. A bug in the translator is not a refusal and
      ;; must not be reported as one.
      (handler-case (sel.sql:translate unbound "mariadb" bindings)
        (sel.sql:sql-error (e)
          (format t "   translate    => ~a~%" (sel.sql:sql-error-code e)))))

    ;; 6 — what a fragment knows about itself ----------------------------------------
    ;; A caveat is the map saying "this dialect's answer may differ from SEL's
    ;; here". An empty list is the layer promising it does not.

    (format t "6. the fragment~%")
    (format t "   kind         => ~a~%" (symbol-name (sel.sql:fragment-kind frag)))
    (format t "   dialect      => ~a~%" (sel.sql:fragment-dialect frag))
    (format t "   exact        => ~a~%"
            (if (null (sel.sql:fragment-caveats frag)) "TRUE" "FALSE")))
  0)
