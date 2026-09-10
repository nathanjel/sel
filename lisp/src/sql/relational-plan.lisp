;;;; Relational Plan IR for statement compilation.
;;;;
;;;; Represents the structured relational query before emitting dialect SQL.
;;;; Captures sources, projections, filter predicates, orderings, and pagination.

(in-package #:sel.sql)

(defstruct (relational-plan (:constructor make-relational-plan))
  (source-name "" :type string)
  (source-relation nil)
  (source-table "")
  (source-alias nil)
  (correlate nil)
  (distinct nil :type boolean)
  (select-cols nil)
  (projections nil)
  (filters '() :type list)
  (order-by '() :type list)
  (limit nil)
  (offset nil))
