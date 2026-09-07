;;;; Adding a SQL flavour — teaching the translator about your database, from
;;;; Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/dialect/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; The shipped map covers four targets over two bases. A deployment is rarely
;;;; exactly one of them: a driver wants numbered placeholders, a function is
;;;; spelled differently, an extension is not installed. A dialect is registered
;;;; rather than forked, so what you write is only the difference.
;;;;
;;;; The four files beside this one print byte-identical output;
;;;; tools/check-examples.sh diffs them.
;;;;
;;;; The visible difference here is the load below. boot.lisp brings in
;;;; :SEL-LANG, the evaluator alone; the map lives in the separate system
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
  (let* ((rule (sel:compile-source "NAME $== \"ok\" AND TOTAL > 10.00"))
         ;; An alist rather than a hash table: the bindings are given in an
         ;; order and read back in one, and a literal alist says that.
         (bindings (list (cons "NAME"  (sel.sql:binding-column "name" "t" :text))
                         (cons "TOTAL" (sel.sql:binding-column "total" "t" :num)))))
    (flet ((sql-in (dialect)
             (sel.sql:as-condition (sel.sql:translate rule dialect bindings) :params)))

      ;; 1 — what ships --------------------------------------------------------

      (format t "1. what ships~%")
      (format t "   targets      => ~{~a~^ ~}~%" (sel.sql:dialects))
      (format t "   postgresql   => ~{~a~^ -> ~}~%" (sel.sql:dialect-chain "postgresql"))

      ;; 2 — a flavour of your own ---------------------------------------------
      ;; :EXTENDS is the whole mechanism: the new dialect answers for what it
      ;; declares and defers upward for everything else. Two-phase lookup -- the
      ;; whole overlay chain, then the whole shipped chain -- so an override
      ;; never half-applies.

      (format t "2. a flavour of your own~%")
      (sel.sql:define-dialect "pg-libpq"
        ;; :TARGET T because a base is not a target; this is a server. The
        ;; lexical map is keyed by strings, which are the map document's own
        ;; key names rather than this host's vocabulary.
        '(:extends "postgresql"
          :version "15"
          :target t
          :lexical (("placeholder" . "${n}"))))   ; libpq numbers its parameters
      (format t "   targets      => ~{~a~^ ~}~%" (sel.sql:dialects))
      (format t "   chain        => ~{~a~^ -> ~}~%" (sel.sql:dialect-chain "pg-libpq"))
      (format t "   base         => ~a~%" (sql-in "postgresql"))
      (format t "   pg-libpq     => ~a~%" (sql-in "pg-libpq"))

      ;; 3 — spelling one function differently ---------------------------------
      ;; {*} is every argument; {0}, {1} pick them out. Note the slots are
      ;; ZERO-based while every position SEL reports is one-based -- these are
      ;; template holes, not SEL positions. The entry also says what it returns,
      ;; because the translator infers kinds and will not guess.
      ;;
      ;; The section is a keyword, :FUNCS, where the other hosts pass the string
      ;; "funcs". The sections are a closed set, and every closed set in this
      ;; host is spelled as a keyword -- :NUM, :TEXT, :PARAMS -- so the map's
      ;; sections are spelled that way too rather than as strings nothing else
      ;; here is keyed by.

      (format t "3. one function, respelled~%")
      (sel.sql:define-entry "pg-libpq" :funcs "UPPER"
                            '(:tpl "UPPER({0} COLLATE \"C\")" :ret "TEXT"))
      (format t "   upper        => ~a~%"
              (sel.sql:as-value
               (sel.sql:translate (sel:compile-source "UPPER(NAME)") "pg-libpq" bindings)))

      ;; 4 — withdrawing what a deployment does not have ------------------------
      ;; A NIL entry withdraws it. This is not the same as leaving it unmapped:
      ;; it is the map saying "not here", and the rule is refused rather than
      ;; emitted against a function the server does not have.

      (format t "4. withdrawing an entry~%")
      (sel.sql:define-entry "pg-libpq" :funcs "RMATCH" nil)
      (let ((re (sel:compile-source "RMATCH('^a', NAME)")))
        (format t "   postgresql   => ~a~%"
                (if (null (sel.sql:try-translate re "postgresql" bindings))
                    "refused" "translated"))
        (format t "   pg-libpq     => ~a~%"
                (if (null (sel.sql:try-translate re "pg-libpq" bindings))
                    "refused" "translated")))

      ;; 5 — a builder, for what a template cannot say --------------------------
      ;; The escape hatch. It receives the emitter and the already-rendered
      ;; arguments, and returns a fragment, so it can do what no string with
      ;; holes in it can. The emitter here is the DIALECT: this host's emit
      ;; functions all take a dialect as their first argument, so that is what a
      ;; builder is handed, where Python is handed `self.emit` and JS an object
      ;; whose dialect() it calls.
      ;;
      ;; Splice the argument's parts rather than its rendered SQL. A part list
      ;; is strings alternating with parameter slots, so splicing keeps a bound
      ;; value bound; flattening it to a string first would inline whatever the
      ;; argument carried and quietly turn a prepared statement back into
      ;; concatenation.
      ;;
      ;; %FRAGMENT is internal, hence the double colon: SEL.SQL exports the
      ;; fragment type and its accessors but no constructor, so a builder that
      ;; returns one has to reach for the internal name.

      (format t "5. a builder~%")
      (sel.sql:define-builder
       "pg-libpq" :funcs "LEN"
       (lambda (dialect args pos)
         (declare (ignore pos))
         (sel.sql::%fragment (append (list "length(")
                                     (sel.sql:fragment-parts (first args))
                                     (list ")"))
                             :num dialect)))
      (format t "   len          => ~a~%"
              (sel.sql:as-value
               (sel.sql:translate (sel:compile-source "LEN(NAME)") "pg-libpq" bindings)))

      ;; 6 — putting it back ----------------------------------------------------
      ;; MAP-RESET drops every registration and leaves the shipped map. Worth
      ;; knowing in a test suite: a registration that leaks into the next test is
      ;; a test that passes for the wrong reason.

      (format t "6. reset~%")
      (sel.sql:map-reset)
      (format t "   targets      => ~{~a~^ ~}~%" (sel.sql:dialects))
      (format t "   pg-libpq     => ~a~%"
              (if (sel.sql:dialect-exists-p "pg-libpq") "still there" "gone"))))
  0)
