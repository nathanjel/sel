;;;; SEL -> SQL. The design is in docs/SQL-TRANSLATION.md; the dialect map
;;;; format is normative in sql/MAP.md and the error codes in sql/errors.md.
;;;;
;;;; A sibling package rather than part of #:SEL, for the reason the JS host
;;;; keeps its translator behind a separate entry point: a program that only
;;;; wants the evaluator should not carry the map. It reaches the evaluator's
;;;; internals with SEL:: -- +MAX-DEPTH+, ASCII-UPCASE, VALIDATE-PATTERN, the
;;;; node accessors -- which is what every other host does too (Python imports
;;;; from ..eval, PHP from the Sel namespace). None of that is public surface in
;;;; any host, and making it public here would give Lisp an API the others lack.

(defpackage #:sel.sql
  (:use #:common-lisp)
  (:export
   ;; Errors
   #:sql-error
   #:sql-error-code
   #:sql-error-message
   #:sql-error-line
   #:sql-error-col
   #:sql-error-offset

   ;; Fragments
   #:fragment
   #:fragment-p
   #:fragment-kind
   ;; The part list and its values, for a harness that checks every slot has a
   ;; value and every value is emitted. C++ exposes parts() and params() for the
   ;; same reason.
   #:fragment-parts
   #:fragment-params
   #:fragment-dialect
   #:fragment-caveats
   #:as-value
   #:as-condition
   #:bindings

   ;; Bindings
   #:binding
   #:binding-p
   #:binding-column
   #:binding-raw
   #:binding-columns
   #:binding-relation
   #:binding-relation-query
   #:binding-value

   ;; The map, for an application that extends it
   #:define-dialect
   #:define-entry
   #:define-builder
   #:map-reset
   #:dialect-exists-p
   #:dialect-targets
   #:dialect-chain
   #:dialect-version
   #:dialect-lexical
   #:dialect-entry

   ;; The layer itself
   #:translate
   #:try-translate
   #:dialects))
