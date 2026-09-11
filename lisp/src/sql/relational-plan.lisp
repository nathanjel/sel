;;;; Relational Plan IR for statement compilation.
;;;;
;;;; Represents the structured relational query before emitting dialect SQL.
;;;; Captures sources, projections, filter predicates, orderings, and pagination.

(in-package #:sel.sql)

(defstruct (join-plan (:constructor make-join-plan))
  (type :inner :type symbol) ; :inner or :left
  (source-name "" :type string)
  (source-relation nil)
  (source-table "")
  (source-alias nil)
  (left-binder nil)
  (right-binder nil)
  (on-pred nil)
  (pos nil))

(defstruct (relational-plan (:constructor make-relational-plan))
  (source-name "" :type string)
  (source-relation nil)
  (source-table "")
  (source-alias nil)
  (source-subquery nil)
  (correlate nil)
  (joins '() :type list)
  (distinct nil :type boolean)
  (select-cols nil)
  (projections nil)
  (filters '() :type list)
  (group-by nil)
  (having '() :type list)
  (aggregate-aliases nil)
  (order-by '() :type list)
  (limit nil)
  (offset nil))

