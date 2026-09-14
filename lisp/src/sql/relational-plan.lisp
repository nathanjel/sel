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
  ;; A BUCKET without a projection leaves the plan :open: its SQL rows are the
  ;; group keys, which is not what SEL's buckets are (a map of member rows), so
  ;; the next MAP is folded into the bucket as its projection -- the one SQL
  ;; shape a bucket has. Any other step first turns it :sealed: the members
  ;; are gone for good, and a MAP after that is refused rather than evaluated
  ;; over rows SEL would have called groups.
  (bucket nil)
  (having '() :type list)
  (aggregate-aliases nil)
  (order-by '() :type list)
  (limit nil)
  (offset nil))

