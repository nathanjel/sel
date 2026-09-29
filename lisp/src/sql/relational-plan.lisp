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
  ;; The names the LINK gives its sides, besides `_1` and `_2` (spec §7.4).
  (left-names '() :type list)
  (right-names '() :type list)
  (on-pred nil)
  (pos nil))

(defstruct (relational-plan (:constructor make-relational-plan))
  (source-name "" :type string)
  ;; The variable the pipeline starts from, which names the first
  ;; three-argument LINK's left side; NIL once a LINK has joined.
  (root-name nil)
  (source-relation nil)
  (source-table "")
  (source-alias nil)
  (source-subquery nil)
  ;; A derived table with no LIMIT beside its ORDER BY does not keep the order, so a
  ;; step that needs the rows in that order (a BUCKET's groups, a LINK's rows, a
  ;; later sort's ties) cannot be built on it.
  (order-dropped nil)
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
  ;; Whether the grouping was written as a bare BUCKET (with or without the
  ;; MAP that closes it). A bare bucket's key is an index key: SEL refuses a
  ;; boolean, binary, list or record key, so the translator must too.
  (bare-key nil)
  (having '() :type list)
  (order-by '() :type list)
  (limit nil)
  (offset nil)
  ;; JOIN-ROWS' models of the joined rows, as (JOIN-COUNT . ROWS).
  (join-rows-cache nil))

