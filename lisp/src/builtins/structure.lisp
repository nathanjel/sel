(in-package #:sel)

;;; A scalar with no children behaves as a one-element list containing itself,
;;; consistent with scalar context (§3.2). A NONE with no children is genuinely
;;; empty — that is what FILTER returns when nothing matched, and ALL over it
;;; must be TRUE rather than a scalar-context failure.
(defun aggregate-elements (v)
  (cond ((plusp (value-size v)) (value-entries v))
        ((eq (value-kind v) :none) '())
        (t (list (cons "1" v)))))

(define-builtin "COUNT" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (value-size (args-val a 0)))))

(define-builtin "INDEXES" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value (mapcar #'%text (value-keys (args-val a 0))))))

(define-builtin "HAS" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (value-has (args-val a 0) (args-text a 1)))))

(define-builtin "LIST" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value
     (loop for i below (args-count a)
           collect (value-copy (args-val a i))))))

(define-builtin "RECORD" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((n (args-count a))
           (num-fields (ash n -1)))
      (if (zerop n)
          (make-none)
          (let* ((keys (loop for i from 0 below n by 2 collect (args-text a i)))
                 (prepared (args-record-shape a))
                 (matches (and prepared (equal keys (record-shape-keys prepared)))))
            (if (or matches (= (length (remove-duplicates keys :test #'string=)) num-fields))
                (let* ((shape (if matches prepared (get-record-shape keys)))
                       (storage (make-array num-fields)))
                  (loop for i from 0 below n by 2
                        for slot-idx from 0
                        do (setf (svref storage slot-idx) (value-copy (args-val a (1+ i)))))
                  (%make-shaped-value shape storage))
                (let ((rec (make-none)))
                  (loop for i from 0 below n by 2
                        do (value-set rec (args-text a i) (value-copy (args-val a (1+ i)))))
                  rec)))))))   ; even count: spec/builtins.json

(define-builtin "TAKE" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0))
          (count (args-non-neg-int a 1)))
      (if (or (zerop count) (value-null-p val))
          (make-list-value nil)
          (cond
            ((and (value-is-list val) (value-storage val))
             (let* ((storage (value-storage val))
                    (limit (min count (length storage))))
               (%make-list-value-fast (subseq storage 0 limit))))
            (t
             (let* ((ents (aggregate-elements val))
                    (limit (min count (length ents))))
               (make-list-value
                (loop for cell in (subseq ents 0 limit)
                      collect (cdr cell))))))))))

(define-builtin "DROP" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0))
          (count (args-non-neg-int a 1)))
      (if (value-null-p val)
          (make-list-value nil)
          (cond
            ((and (value-is-list val) (value-storage val))
             (let* ((storage (value-storage val))
                    (n (length storage)))
               (if (>= count n)
                   (make-list-value nil)
                   (%make-list-value-fast (subseq storage count n)))))
            (t
             (let ((ents (aggregate-elements val)))
               (if (>= count (length ents))
                   (make-list-value nil)
                   (make-list-value
                    (loop for cell in (nthcdr count ents)
                          collect (cdr cell)))))))))))

(defmacro for-each-collection-item ((item-var coll) &body body)
  (let ((v-g (gensym "COLL"))
        (st-g (gensym "ST"))
        (i-g (gensym "I"))
        (c-g (gensym "C")))
    `(let ((,v-g ,coll))
       (cond
         ((and (value-is-list ,v-g) (value-storage ,v-g))
          (let ((,st-g (value-storage ,v-g)))
            (loop for ,i-g from 0 below (length ,st-g)
                  for ,item-var = (svref ,st-g ,i-g)
                  do ,@body)))
         (t
          (dolist (,c-g (aggregate-elements ,v-g))
            (let ((,item-var (cdr ,c-g)))
              ,@body)))))))

(defun first-collection-item (v)
  (cond
    ((or (value-null-p v) (and (eq (value-kind v) :none) (zerop (value-size v))))
     nil)
    ((and (value-is-list v) (value-storage v) (plusp (length (value-storage v))))
     (svref (value-storage v) 0))
    ((plusp (value-size v))
     (cdr (first (aggregate-elements v))))
    ((not (eq (value-kind v) :none))
     v)
    (t nil)))

(define-builtin "SELECT_COLS" 2 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((val (args-val a 0)))
      (if (value-null-p val)
          (make-list-value nil)
          (let* ((col-count (args-count a))
                 (cols (loop for i from 1 below col-count collect (args-text a i))))
            (flet ((project-row (row)
                     (let ((new-row (make-none)))
                       (dolist (c cols)
                         (when (value-has row c)
                           (value-set new-row c (value-copy (value-get row c)))))
                       new-row)))
              (cond
                ((and (value-is-list val) (value-storage val))
                 (let* ((storage (value-storage val))
                        (n (length storage))
                        (out (make-array n)))
                   (loop for i from 0 below n
                         do (setf (svref out i) (project-row (svref storage i))))
                   (%make-list-value-fast out)))
                (t
                 (make-list-value
                  (loop for (nil . row) in (aggregate-elements val)
                        collect (project-row row)))))))))))

(defun do-dedupe (a ctx)
  (declare (ignore ctx))
  (let ((val (args-val a 0)))
    (if (value-null-p val)
        (make-list-value nil)
        (let ((seen (make-hash-table :test #'eql))
              (out '()))
          (for-each-collection-item (item val)
            (let* ((h (value-hash item))
                   (bucket (gethash h seen)))
              (unless (member item bucket :test #'value-eql)
                (setf (gethash h seen) (cons item bucket))
                (push item out))))
          (make-list-value (nreverse out))))))

(define-builtin "DISTINCT" 1 1
  (lambda (a ctx) (do-dedupe a ctx)))

(define-builtin "DEDUPE" 1 1
  (lambda (a ctx) (do-dedupe a ctx)))

(defun expr-depends-only-on (node allowed-binders)
  "Returns T if all :var references in NODE are members of ALLOWED-BINDERS (case-insensitive)."
  (let ((all-ok t))
    (labels ((walk (n)
               (unless all-ok (return-from walk))
               (when (and n (node-p n))
                 (case (node-kind n)
                   (:var
                    (unless (member (node-s n) allowed-binders :test #'string-equal)
                      (setf all-ok nil)))
                   (:index
                    (walk (node-l n))
                    (walk (node-r n)))
                   (:call
                    (dolist (item (node-items n)) (walk item)))
                   (:bin
                    (walk (node-l n))
                    (walk (node-r n)))
                   (:un
                    (walk (node-l n)))
                   (:group
                    (walk (node-l n)))))))
      (walk node)
      all-ok)))

(defun try-extract-equi-keys (pred-node b1 b2)
  "Checks if PRED-NODE is an equality comparison between an expression on B1 and an expression on B2.
Returns (values left-expr right-expr is-numeric) or NIL."
  (when (and pred-node
             (node-p pred-node)
             (eq (node-kind pred-node) :bin)
             (member (node-s pred-node) '("==" "$==") :test #'string=))
    (let ((l (node-l pred-node))
          (r (node-r pred-node))
          (is-numeric (string= (node-s pred-node) "=="))
          (b1-names (list b1 (string-downcase b1) "_1" "_"))
          (b2-names (list b2 (string-downcase b2) "_2")))
      (cond
        ((and (expr-depends-only-on l b1-names)
              (expr-depends-only-on r b2-names))
         (values l r is-numeric))
        ((and (expr-depends-only-on r b1-names)
              (expr-depends-only-on l b2-names))
         (values r l is-numeric))
        (t nil)))))

;; One key per number, as `==` compares it (spec §7.4): trailing fraction
;; zeros and a negative zero are representation, not value. The plain-integer
;; shortcut is an ASCII check, never DIGIT-CHAR-P, which accepts other scripts.
(defun canonical-numeric-string (sc)
  (declare (type string sc))
  (let ((len (length sc)))
    (if (and (plusp len)
             (or (char/= (char sc 0) #\0) (= len 1))
             (loop for i from 0 below len
                   always (char<= #\0 (char sc i) #\9)))
        sc
        (let ((d (dec-parse sc)))
          (when d
            (let ((digits (dec-digits d))
                  (scale (dec-scale d)))
              (if (zerop digits)
                  "0"
                  (progn
                    (loop while (and (> scale 0) (zerop (mod digits 10)))
                          do (setf digits (floor digits 10))
                             (decf scale))
                    (dec-format (%make-dec (dec-neg d) digits scale))))))))))

;; `_1` and `_2` name a position, not a relation: an argument with no name is
;; bound bare (spec §7.4).
(defun positional-binder-p (name)
  (or (string= name "_1") (string= name "_2")))

(defun extract-join-key (val is-numeric)
  (when (and val (not (value-null-p val)))
    (if is-numeric
        (let ((sc (if (eq (value-kind val) :none)
                      (when (value-children val)
                        (let ((fst (cdr (first (value-children val)))))
                          (when fst (value-scalar fst))))
                      (value-scalar val))))
          (when (and sc (stringp sc))
            (canonical-numeric-string sc)))
        (when (eq (value-kind val) :text)
          (value-scalar val)))))

(defun make-null-record (sample-row tbl-name)
  "An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the first
right element as bound -- SAMPLE-ROW, already extended with the name -- every
field NULL; with no right elements, just the name keys; with no name either,
NULL. VALUE-SET on a key that exists keeps its place."
  (let ((null-rec (make-none)))
    (when sample-row
      (dolist (k (value-keys sample-row))
        (value-set null-rec k (make-none))))
    (when (and (null sample-row) tbl-name (not (positional-binder-p tbl-name)))
      (let ((low (string-downcase tbl-name)))
        (unless (value-has null-rec tbl-name) (value-set null-rec tbl-name (make-none)))
        (when (and (string/= tbl-name low) (not (value-has null-rec low)))
          (value-set null-rec low (make-none)))))
    null-rec))

;; Keep ownership global, not on record shapes: source/destination chains must
;; remain bounded. Two lookup levels avoid a fresh composite key on every hit
;; while preserving multiple table names per source shape.
;; Rebuild derived metadata on reload, including upgrades from older layouts.
(defparameter *alias-plan-cache* (make-hash-table :test #'eq))
(defparameter *alias-plan-cache-count* 0)

(defun ensure-row-table-alias (row tbl-name)
  (if (or (null tbl-name) (positional-binder-p tbl-name) (value-has row tbl-name))
      row
      (cond
        ((value-shape row)
         (let* ((old-shape (value-shape row))
                (plans (gethash old-shape *alias-plan-cache*))
                (cached (and plans (gethash tbl-name plans))))
           (multiple-value-bind (new-shape is-diff old-len)
               (if cached
                   (values (first cached) (second cached) (third cached))
                   (let* ((low (string-downcase tbl-name))
                          ;; The lowercase only where the element lacks it.
                          (diff (and (string/= tbl-name low)
                                     (not (gethash low (record-shape-key-map old-shape)))))
                          (old-keys (record-shape-keys old-shape))
                          (new-keys (if diff
                                        (append old-keys (list tbl-name low))
                                        (append old-keys (list tbl-name))))
                          (ns (get-record-shape new-keys))
                          (olen (record-shape-size old-shape)))
                     (when (and (<= (length new-keys) +shape-cache-max-keys+)
                                (<= (loop for key in new-keys sum (length key))
                                    +shape-cache-max-chars+))
                       (when (>= *alias-plan-cache-count* +shape-cache-entries+)
                         (clrhash *alias-plan-cache*)
                         (setf *alias-plan-cache-count* 0 plans nil))
                       (unless plans
                         (setf plans (make-hash-table :test #'equal)
                               (gethash old-shape *alias-plan-cache*) plans))
                       (setf (gethash tbl-name plans) (list ns diff olen))
                       (incf *alias-plan-cache-count*))
                     (values ns diff olen)))
             (let* ((old-storage (value-storage row))
                    (new-storage (make-array (record-shape-size new-shape))))
               (loop for i from 0 below old-len
                     do (setf (svref new-storage i) (svref old-storage i)))
               (setf (svref new-storage old-len) row)
               (when is-diff
                 (setf (svref new-storage (1+ old-len)) row))
               (%make-shaped-value new-shape new-storage)))))
        (t
         (let* ((low (string-downcase tbl-name))
                (diff (and (string/= tbl-name low) (not (value-has row low))))
                (extra (if diff
                           (list (cons tbl-name row) (cons low row))
                           (list (cons tbl-name row))))
                (new-children (append (value-children row) extra)))
           (%value-with-children (value-kind row) (value-scalar row) new-children (value-is-list row)))))))

(defconstant +join-scalar+ 0)
(defconstant +join-null+ 1)
(defconstant +join-nested+ 2)

(declaim (inline join-category))
(defun join-category (v)
  "A field's category for the joined row (spec §7.4): a nested record (a record
with a field) is carried, anything else is a scalar field -- and a right
scalar that is NULL is not promoted."
  (cond ((or (not (eq (value-kind v) :none)) (value-is-list v)) +join-scalar+)
        ((plusp (value-size v)) +join-nested+)
        (t +join-null+)))

(defun join-binder-keys (name positional)
  (let ((low (string-downcase name)))
    (append (list name)
            (when (string/= low name) (list low))
            (when (string/= name positional) (list positional)))))

(defun join-entries (v)
  (if (and v (plusp (value-size v)) (not (value-is-list v)))
      (loop for k in (value-keys v) collect (cons k (value-get v k)))
      '()))

(defun make-joined-row (r1 r2 b1 b2 null-r2)
  "One joined row, from THIS pair's two elements (spec §7.4): the nested
records the left element carries, the left binders, the right binders, then
the left element's scalar fields whose names (ASCII-case-insensitively) are
not the right element's, then the right element's non-NULL scalar fields
whose names are not the left's -- each key once, where it first occurred, a
binder key holding the row this LINK bound. R2 is NIL for an unmatched
LINK_LEFT row, whose right element is NULL-R2 and promotes nothing."
  (let ((entries '())
        (slot (make-hash-table :test #'equal)))
    (labels ((put (k v)
               (unless (gethash k slot)
                 (let ((cell (cons k v)))
                   (setf (gethash k slot) cell)
                   (push cell entries))))
             (bind (k v)
               (let ((cell (gethash k slot)))
                 (if cell (setf (cdr cell) v) (put k v)))))
      (let* ((left-entries (join-entries r1))
             (rside (or r2 null-r2 (make-none)))
             (right-entries (join-entries rside)))
        (loop for (k . v) in left-entries
              when (= (join-category v) +join-nested+) do (put k v))
        (dolist (name (join-binder-keys b1 "_1")) (bind name r1))
        (dolist (name (join-binder-keys b2 "_2")) (bind name rside))
        (let ((right-names (mapcar (lambda (e) (string-upcase (car e))) right-entries)))
          (loop for (k . v) in left-entries
                unless (or (= (join-category v) +join-nested+)
                           (member (string-upcase k) right-names :test #'string=))
                  do (put k v)))
        (when r2
          (let ((left-names (mapcar (lambda (e) (string-upcase (car e))) left-entries)))
            (loop for (k . v) in right-entries
                  when (and (= (join-category v) +join-scalar+)
                            (not (member (string-upcase k) left-names :test #'string=)))
                    do (put k v))))))
    (let* ((entries (nreverse entries))
           (keys (mapcar #'car entries))
           (shape (get-record-shape keys))
           (storage (make-array (length keys))))
      (loop for (nil . val) in entries
            for idx from 0
            do (setf (svref storage idx) val))
      (%make-shaped-value shape storage))))

(defun make-join-plan (r1 rside matched b1 b2)
  "The joined row of a pair as a plan over the two elements' storage, for every
pair whose elements have these shapes and field categories: the output shape
and a vector of (op . slot) -- :l slot, :r slot, :left, :right.
MAKE-JOINED-ROW is the rule; this is it, compiled."
  (let ((keys '()) (ops '()) (at (make-hash-table :test #'equal)) (n 0))
    (labels ((put (k op)
               (unless (gethash k at)
                 (setf (gethash k at) n)
                 (incf n)
                 (push k keys) (push op ops)))
             (bind (k op)
               (let ((i (gethash k at)))
                 (if i
                     (setf (nth (- n 1 i) ops) op)
                     (put k op)))))
      (let* ((lkeys (record-shape-keys (value-shape r1)))
             (lstore (value-storage r1))
             (rkeys (when (value-shape rside) (record-shape-keys (value-shape rside))))
             (rstore (value-storage rside)))
        (loop for k in lkeys for i from 0
              when (= (join-category (svref lstore i)) +join-nested+) do (put k (cons :l i)))
        (dolist (name (join-binder-keys b1 "_1")) (bind name (cons :left nil)))
        (dolist (name (join-binder-keys b2 "_2")) (bind name (cons :right nil)))
        (let ((right-names (mapcar #'string-upcase rkeys)))
          (loop for k in lkeys for i from 0
                unless (or (= (join-category (svref lstore i)) +join-nested+)
                           (member (string-upcase k) right-names :test #'string=))
                  do (put k (cons :l i))))
        (when matched
          (let ((left-names (mapcar #'string-upcase lkeys)))
            (loop for k in rkeys for i from 0
                  when (and (= (join-category (svref rstore i)) +join-scalar+)
                            (not (member (string-upcase k) left-names :test #'string=)))
                    do (put k (cons :r i)))))))
    (cons (get-record-shape (nreverse keys)) (coerce (nreverse ops) 'simple-vector))))

;; Only two facts about a field decide a joined row (spec §7.4): on the left,
;; whether it is a nested record (carried first) or not; on the right, whether
;; it is a non-NULL scalar (promoted).
(declaim (inline join-left-nested-p))
(defun join-left-nested-p (v)
  (and (eq (value-kind v) :none) (not (value-is-list v)) (plusp (value-count v))))

(defstruct (join-plan (:constructor %make-join-plan))
  "A compiled joined row: SHAPE, and per output key an op -- 0 a left field
that was not a nested record, 4 one that was, 1 a right field, 2 the left
element, 3 the right one -- and its SLOT; what the plan assumed and the row
does not show: LREST, the left fields it leaves out as (slot . nested), and
RKEPT, the right fields it leaves out for being NULL or a record."
  shape
  (ops (make-array 0 :element-type 'fixnum) :type (simple-array fixnum (*)))
  (slots (make-array 0 :element-type 'fixnum) :type (simple-array fixnum (*)))
  (lrest '() :type list)
  (rkept '() :type list))

(defun compile-join-plan (r1 rside matched b1 b2)
  (let* ((plan (make-join-plan r1 rside matched b1 b2))
         (entries (cdr plan))
         (n (length entries))
         (ls (value-storage r1))
         (ops (make-array n :element-type 'fixnum :initial-element 0))
         (slots (make-array n :element-type 'fixnum :initial-element 0))
         (copied (make-array (length ls) :initial-element nil))
         (lrest '())
         (rkept '()))
    (loop for i from 0 below n
          for (op . slot) = (svref entries i)
          do (setf (aref ops i) (ecase op
                                   (:l (setf (svref copied slot) t)
                                    (if (join-left-nested-p (svref ls slot)) 4 0))
                                   (:r 1) (:left 2) (:right 3))
                   (aref slots i) (or slot 0)))
    (loop for i from (1- (length ls)) downto 0
          unless (svref copied i) do (push (cons i (join-left-nested-p (svref ls i))) lrest))
    ;; Right fields the left does not name that were left out for being NULL
    ;; or records must still be, for the plan to hold. (A scalar left out
    ;; because its name is already a key of the row stays out whatever it
    ;; holds; so does one named like a binder key, which the binder holds.)
    (when matched
      (let ((left-names (mapcar #'string-upcase (record-shape-keys (value-shape r1))))
            (binder-names (append (join-binder-keys b1 "_1") (join-binder-keys b2 "_2")))
            (rs (value-storage rside)))
        (loop for k in (record-shape-keys (value-shape rside)) for i from 0
              for v = (svref rs i)
              when (and (not (member (string-upcase k) left-names :test #'string=))
                        (not (member k binder-names :test #'string=))
                        (eq (value-kind v) :none) (not (value-is-list v)))
                do (push i rkept))))
    (%make-join-plan :shape (car plan) :ops ops :slots slots
                     :lrest lrest :rkept (nreverse rkept))))

(defun join-plan-build (plan r1 rside check-left check-right)
  "The row PLAN makes of R1 and RSIDE, or NIL when the pair breaks its
assumptions: with CHECK-LEFT, that each left field is (or is not) a nested
record as it was; with CHECK-RIGHT, that a right field it copies is a
non-NULL scalar -- checked as it is copied -- and that one it leaves out for
being NULL or a record still is one."
  (declare (optimize (speed 3) (safety 1)) (type join-plan plan))
  (let ((ls (value-storage r1))
        (rs (value-storage rside)))
    (declare (simple-vector ls rs))
    (when check-left
      (loop for (i . nested) in (join-plan-lrest plan)
            unless (eq (join-left-nested-p (svref ls i)) nested)
              do (return-from join-plan-build nil)))
    (when check-right
      (loop for i in (join-plan-rkept plan)
            for v = (svref rs i)
            unless (and (eq (value-kind v) :none) (not (value-is-list v)))
              do (return-from join-plan-build nil)))
    (let* ((ops (join-plan-ops plan))
           (slots (join-plan-slots plan))
           (n (length ops))
           (storage (make-array n)))
      (declare (type (simple-array fixnum (*)) ops slots) (simple-vector storage) (fixnum n))
      (dotimes (i n)
        (setf (svref storage i)
              (case (aref ops i)
                (0 (let ((v (svref ls (aref slots i))))
                     (when (and check-left (join-left-nested-p v))
                       (return-from join-plan-build nil))
                     v))
                (4 (let ((v (svref ls (aref slots i))))
                     (when (and check-left (not (join-left-nested-p v)))
                       (return-from join-plan-build nil))
                     v))
                (1 (let ((v (svref rs (aref slots i))))
                     (when (and check-right (eq (value-kind v) :none) (not (value-is-list v)))
                       (return-from join-plan-build nil))
                     v))
                (2 r1)
                (t rside))))
      (%make-shaped-value (join-plan-shape plan) storage))))

(defun make-join-flat-test (binder-names)
  "(lambda (row)): whether every field of ROW is a non-NULL scalar, but for
those named like a binder key (BINDER-NAMES), which no joined row takes from
it. The fields to read are worked out once per shape."
  (let ((last-shape nil) (slots '()))
    (lambda (row)
      (let ((shape (value-shape row))
            (storage (value-storage row)))
        (and shape storage
             (progn
               (unless (eq shape last-shape)
                 (setf last-shape shape
                       slots (loop for k in (record-shape-keys shape) for i from 0
                                   unless (member k binder-names :test #'string=) collect i)))
               (loop for i in slots
                     for v = (svref storage i)
                     never (and (eq (value-kind v) :none) (not (value-is-list v))))))))))

(defun make-join-projector (b1 b2 null-r2 facts)
  "(lambda (r1 r2)): MAKE-JOINED-ROW, through compiled plans. For each pair of
shapes the plans built so far are tried in turn; each checks the two facts it
assumed of the fields it reads -- every left field, and the right fields whose
names neither the left nor a binder has -- as it copies them, and a pair none
fits gets its own plan from the rule (MAKE-JOIN-PLAN). A left row's matches
come one after another, so the left checks are made once per left row. When
the join has seen that every right row is flat (MAKE-JOIN-FLAT-TEST), it sets
(CAR FACTS) and the right checks are skipped: a plan made from a flat row
holds for every flat row of its shape."
  (let ((plans (make-hash-table :test #'equal))
        (last-left nil) (last-plan nil) (last-list nil)
        (pair-l nil) (pair-r nil) (pair-matched nil))
    (lambda (r1 r2)
      (block project
        (let ((rside (or r2 null-r2)))
          (if (or (null (value-shape r1)) (null (value-storage r1))
                  (null rside) (null (value-shape rside)) (null (value-storage rside)))
              (make-joined-row r1 r2 b1 b2 null-r2)
              (let ((lshape (value-shape r1))
                    (rshape (value-shape rside))
                    (matched (and r2 t))
                    (list nil))
                (flet ((remember (plan row)
                         (setf last-left r1 last-plan plan last-list list
                               pair-l lshape pair-r rshape pair-matched matched)
                         row))
                  ;; The same shapes as the last pair: its plan first, checking
                  ;; the left row only when it is a new one.
                  (if (and last-plan (eq lshape pair-l) (eq rshape pair-r) (eq matched pair-matched))
                      (let ((row (join-plan-build last-plan r1 rside (not (eq last-left r1)) (not (car facts)))))
                        (when row
                          (setf last-left r1)
                          (return-from project row))
                        (setf list last-list))
                      (let ((key (list lshape rshape matched)))
                        (setf list (or (gethash key plans)
                                       (setf (gethash key plans) (list :plans))))))
                  (dolist (plan (cdr list))
                    (let ((row (join-plan-build plan r1 rside t (not (car facts)))))
                      (when row (return-from project (remember plan row)))))
                  (let ((plan (compile-join-plan r1 rside matched b1 b2)))
                    (setf (cdr (last list)) (list plan))
                    (remember plan (join-plan-build plan r1 rside nil nil)))))))))))

(defun single-relation-name (node)
  "Returns the relation name if NODE represents a single relation (possibly filtered/mapped), but NIL if it involves a join."
  (cond
    ((null node) nil)
    ((and (node-p node) (eq (node-kind node) :var))
     (node-s node))
    ((and (node-p node) (eq (node-kind node) :call) (node-items node))
     (let ((op (node-s node)))
       (if (member op '("LINK" "LINK_LEFT") :test #'string=)
           nil
           (single-relation-name (first (node-items node))))))
    (t nil)))

;;; --- the join pre-filter (SEL-0049, SEL-0050, SEL-0052) ---------------------
;;;
;;; A FILTER over a LINK hands the join its conjuncts (LEADING-FIELD-CONJUNCTS,
;;; called from FILTER in aggregate.lisp); the join pre-applies to its left
;;; rows those whose fields no right side carries, so the joined rows they
;;; would have produced -- all dropped by the same conjunct -- are never built.
;;; Decided from the rows, at run time, so the physical tree stays a function
;;; of the AST. docs/EXTENDING.md states the rule for every host.

(defstruct jconj
  node            ; a conjunct of the FILTER body, in the tree
  field-only      ; reads nothing but fields of the row
  fields          ; upper-cased names; `_["orders"]["x"]` reads ORDERS
  has-total       ; a comparison of literals and bare field reads
  total           ; ((field-as-written . numeric-p) ...)
  pushed          ; the physical optimiser pushed it below already
  binder)         ; the FILTER's element name

;; stages: ((binder . jconjs) ...); obligations: JOIN-OBLIGATIONs.
(defstruct join-prefilter stages deep above obligations)
(defstruct join-report applied errored)       ; applied: EQ hash table of nodes
;; names: the member names the side's row is bound under (never `_1`/`_2`).
(defstruct join-side value keys first nullable names (facts (make-hash-table :test #'equal)))
;; A join's left key, handed down with its conjuncts: the join that drops
;; rows must prove it cannot raise on them (JOIN-KEYS-SAFE-P).
(defstruct join-obligation key row-names outer)

(defparameter +text-compare+ '("$==" "$!=" "$<" "$<=" "$>" "$>="))
(defparameter +num-compare+ '("==" "!=" "<" "<=" ">" ">="))

(defun leading-field-conjuncts (body binder)
  "Every AND-conjunct of a FILTER body, in order, as JCONJs."
  (let ((conjuncts '())
        (node body))
    (loop while (and node (eq (node-kind node) :bin) (string= (node-s node) "AND"))
          do (push (node-r node) conjuncts)
             (setf node (node-l node)))
    (push node conjuncts)
    ;; The element is the binder, exactly as named (names are canonical):
    ;; under an explicit binder `_` is not the element.
    (labels ((row-var-p (n)
               (and n (eq (node-kind n) :var) (string= (node-s n) binder)))
             (bare-read-p (n)
               (and n (eq (node-kind n) :index) (row-var-p (node-l n))
                    (node-r n) (eq (node-kind (node-r n)) :text))))
      (loop for c in conjuncts
            collect
            (let ((fields '()))
              (labels ((reads-only-fields (n)
                         (cond ((null n) t)
                               ((eq (node-kind n) :index)
                                (cond ((bare-read-p n)
                                       (pushnew (string-upcase (node-s (node-r n))) fields :test #'string=)
                                       t)
                                      ((and (node-l n) (eq (node-kind (node-l n)) :index))
                                       (and (reads-only-fields (node-l n)) (reads-only-fields (node-r n))))
                                      (t nil)))
                               ((member (node-kind n) '(:num :text :bool)) t)
                               ((eq (node-kind n) :bin)
                                (and (reads-only-fields (node-l n)) (reads-only-fields (node-r n))))
                               ((eq (node-kind n) :un) (reads-only-fields (node-l n)))
                               (t nil))))
                (let* ((field-only (and (reads-only-fields c) fields t))
                       (compare (and c (eq (node-kind c) :bin)
                                     (or (member (node-s c) +text-compare+ :test #'string=)
                                         (member (node-s c) +num-compare+ :test #'string=))))
                       (numeric (and compare (member (node-s c) +num-compare+ :test #'string=) t))
                       (has-total compare)
                       (total '()))
                  (when compare
                    (dolist (operand (list (node-l c) (node-r c)))
                      (cond ((and operand (member (node-kind operand) '(:num :text)))
                             ;; A text literal is not a number: it raises on every row.
                             (when (and numeric (eq (node-kind operand) :text))
                               (setf has-total nil)))
                            ((bare-read-p operand)
                             (push (cons (node-s (node-r operand)) numeric) total))
                            (t (setf has-total nil)))))
                  (make-jconj :node c :field-only field-only :binder binder
                              :fields (and field-only fields)
                              :has-total has-total :total (and has-total (nreverse total))))))))))

(defun join-pure-source-p (node)
  "Whether evaluating NODE is observable only through its value: no
assignment, sequence, host function or ABORT -- so it may run out of order."
  (or (null node)
      (case (node-kind node)
        ((:var :num :text :bool :null) t)
        ((:index :bin) (and (join-pure-source-p (node-l node)) (join-pure-source-p (node-r node))))
        (:un (join-pure-source-p (node-l node)))
        (:list (every #'join-pure-source-p (node-items node)))
        (:call (and (manifest-entry (string-upcase (node-s node)))
                    (not (string-equal (node-s node) "ABORT"))
                    (every #'join-pure-source-p (node-items node))))
        (t nil))))

(defun join-stage-walk (stages owned-here total-here pushed-held)
  "The JCONJs a join may pre-apply to its left rows, in stage order, and where
the walk stopped as (stage-index . conjunct-index), or NIL. One whose fields
are all the left rows' is applied; one reading a right side's field ends the
walk (AND short-circuits left to right) unless it is total here, in which
case it is passed over for the join above; one reading anything else ends it
too; one the optimiser pushed below is skipped while its tentative FILTER
kept no row on an error."
  (let ((applied '()))
    (loop for (nil . conjuncts) in stages
          for si from 0
          do (loop for c in conjuncts
                   for ci from 0
                   do (cond ((jconj-pushed c)
                             (unless pushed-held
                               (return-from join-stage-walk (values (nreverse applied) (cons si ci)))))
                            ((and (jconj-field-only c) (funcall owned-here (jconj-fields c)))
                             (push c applied))
                            ((and (jconj-has-total c) (funcall total-here (jconj-total c))))
                            (t (return-from join-stage-walk (values (nreverse applied) (cons si ci)))))))
    (values (nreverse applied) nil)))

(defun join-truncate-stages (stages stop)
  (if (null stop)
      stages
      (destructuring-bind (si . ci) stop
        (append (subseq stages 0 si)
                (when (plusp ci)
                  (let ((stage (nth si stages)))
                    (list (cons (car stage) (subseq (cdr stage) 0 ci)))))))))

(defun join-read-self (node names binder)
  "NODE with every `r[\"orders\"]` -- a read through the left binder's own
name (NAMES, upper-cased) on the element BINDER -- replaced by the element:
on the left rows themselves the joined row's member of that name is the row."
  (cond ((null node) nil)
        ((and (eq (node-kind node) :index) (node-l node) (eq (node-kind (node-l node)) :var)
              (string= (node-s (node-l node)) binder)
              (node-r node) (eq (node-kind (node-r node)) :text)
              (member (string-upcase (node-s (node-r node))) names :test #'string=))
         (let ((var (make-node :var (node-pos node))))
           (setf (node-s var) binder)
           var))
        (t (let ((copy (copy-node node)))
             ;; A compiled plan of the original reads the original.
             (setf (node-math-plan copy) nil
                   (node-argument-plan copy) nil
                   (node-record-shape copy) nil
                   (node-l copy) (join-read-self (node-l node) names binder)
                   (node-r copy) (join-read-self (node-r node) names binder)
                   (node-items copy) (mapcar (lambda (i) (join-read-self i names binder)) (node-items node)))
             copy))))

(defun join-row-keys (value bound)
  "An EQUAL hash set of the upper-cased keys of every row (a shape's keys read
once), plus the names the row is bound under in the joined row."
  (let ((keys (make-hash-table :test #'equal))
        (shapes (make-hash-table :test #'eq)))
    (dolist (b bound) (setf (gethash (string-upcase b) keys) t))
    (for-each-collection-item (row value)
      (let ((shape (value-shape row)))
        (if shape
            (unless (gethash shape shapes)
              (setf (gethash shape shapes) t)
              (dolist (k (record-shape-keys shape)) (setf (gethash (string-upcase k) keys) t)))
            (dolist (k (value-keys row)) (setf (gethash (string-upcase k) keys) t)))))
    keys))

(defun make-join-side-of (value keys nullable &optional bound)
  (let ((first-keys (make-hash-table :test #'equal))
        (first (first-collection-item value)))
    (when first
      (dolist (k (value-keys first)) (setf (gethash (string-upcase k) first-keys) t)))
    (make-join-side :value value :keys keys :first first-keys :nullable nullable
                    :names (loop for b in bound
                                 unless (member b '("_1" "_2" "_") :test #'string=)
                                   append (list b (string-downcase b))))))

(defun join-side-fact (side id compute)
  (multiple-value-bind (fact found) (gethash id (join-side-facts side))
    (if found fact (setf (gethash id (join-side-facts side)) (funcall compute)))))

(defun join-side-present-p (side name)
  "NAME is a key of every row, whatever its value: reading it through the
side's member cannot raise."
  (join-side-fact side (cons :present name)
                  (lambda ()
                    (let ((rows 0))
                      (block scan
                        (for-each-collection-item (row (join-side-value side))
                          (incf rows)
                          (unless (value-get row name) (return-from scan nil)))
                        (plusp rows))))))

(defun join-side-any-p (side name)
  "NAME on the first row and on every row as a non-null scalar: what a
promoted join key needs to be read without raising."
  (and (gethash (string-upcase name) (join-side-first side)) (not (join-side-nullable side))
       (join-side-fact side (cons :any name)
                       (lambda ()
                         (block scan
                           (for-each-collection-item (row (join-side-value side))
                             (let ((v (value-get row name)))
                               (when (or (null v) (value-null-p v)
                                         (and (value-children v) (not (value-is-list v))))
                                 (return-from scan nil))))
                           t)))))

(defun join-keys-safe-p (obligations left right above)
  "Whether every handed-down join key -- the left key of each join above this
one that handed its conjuncts down -- cannot raise on a joined row built from
a left row dropped here. As written those joins compute the key for every row
they receive; a row dropped below never reaches them, so an E_NO_KEY there
would be lost. Canonical keys never raise, only the reads do, so presence
suffices: `r[\"m\"][\"f\"]` needs f on every row of m's relation; `r[\"f\"]`
needs f carried, non-null, by every row of the one side below the join that
has it. Anything else is not proved."
  (dolist (ob obligations t)
    (let* ((key (join-obligation-key ob))
           (row-names (join-obligation-row-names ob))
           (below (subseq above 0 (max 0 (- (length above) (join-obligation-outer ob))))))
      (unless (and key (eq (node-kind key) :index) (node-r key) (eq (node-kind (node-r key)) :text) (node-l key))
        (return nil))
      (let ((field (node-s (node-r key)))
            (obj (node-l key)))
        (cond
          ((and (eq (node-kind obj) :index) (node-l obj) (eq (node-kind (node-l obj)) :var)
                (member (node-s (node-l obj)) row-names :test #'string=)
                (node-r obj) (eq (node-kind (node-r obj)) :text))
           (let* ((member (node-s (node-r obj)))
                  (side (find-if (lambda (sd) (member member (join-side-names sd) :test #'string=))
                                 (list* left right below))))
             (if side
                 (unless (join-side-present-p side field) (return nil))
                 (block rows
                   (for-each-collection-item (row (join-side-value left))
                     (let ((inner (value-get row member)))
                       (unless (and inner (value-get inner field)) (return-from join-keys-safe-p nil))))))))
          ((and (eq (node-kind obj) :var) (member (node-s obj) row-names :test #'string=))
           (let ((owners (remove-if-not (lambda (sd) (gethash (string-upcase field) (join-side-keys sd)))
                                        (list* left right below))))
             (unless (and (= (length owners) 1) (join-side-any-p (first owners) field))
               (return nil))))
          (t (return nil)))))))

(defun join-side-total-p (side name numeric)
  "The field NAME (as written) is on SIDE's first row and on every row, as
text (a number is text) or, when NUMERIC, as a number."
  (when (and (gethash (string-upcase name) (join-side-first side)) (not (join-side-nullable side)))
    (let ((id (cons name numeric)))
      (multiple-value-bind (fact found) (gethash id (join-side-facts side))
        (if found
            fact
            (setf (gethash id (join-side-facts side))
                  (block scan
                    (for-each-collection-item (row (join-side-value side))
                      (let ((v (value-get row name)))
                        (unless (and v (eq (value-kind v) :text)) (return-from scan nil))
                        (when numeric
                          (handler-case (as-dec v)
                            (sel-error () (return-from scan nil))))))
                    t)))))))

(defun join-totality-p (reqs left right above)
  "Every (field . numeric) requirement holds over this join's rows: exactly one
side -- the left rows, this right side, or one above -- carries the field at
all, and that side carries it on every row with the kind (a field two sides
carry is promoted from neither, spec §7.4)."
  (loop for (name . numeric) in reqs
        always (let* ((key (string-upcase name))
                      (owners (remove-if-not (lambda (side) (and side (gethash key (join-side-keys side))))
                                             (list* left right above))))
                 (and (= (length owners) 1) (join-side-total-p (first owners) name numeric)))))

(defun do-link (a ctx is-left)
  ;; Taken before anything else is evaluated, so a LINK nested in this one's
  ;; sources cannot pick it up by accident; it is handed down on purpose below.
  (let* ((prefilter (prog1 (context-join-prefilter ctx) (setf (context-join-prefilter ctx) nil)))
         (count (args-count a))
         (stages (and prefilter (join-prefilter-stages prefilter)))
         (deep (and prefilter (join-prefilter-deep prefilter)))
         (above (and prefilter (join-prefilter-above prefilter)))
         (above-keys (let ((h (make-hash-table :test #'equal)))
                       (dolist (side above h)
                         (maphash (lambda (k v) (declare (ignore v)) (setf (gethash k h) t))
                                  (join-side-keys side)))))
         (node0 (args-node a 0))
         (node1 (args-node a 1))
         ;; The keys a side contributes to the joined row include the names
         ;; its row is bound under: `_["products"]` after LINK(PRODUCTS, ...)
         ;; is the right row, not a field of the left ones.
         (b1-names (if (= count 5) (list (args-symbol a 2) "_1") (list (or (single-relation-name node0) "_1") "_1")))
         (b2-names (if (= count 5) (list (args-symbol a 3) "_2") (list (or (single-relation-name node1) "_2") "_2")))
         (kept-before (context-tentative-kept ctx))
         (obligations (and prefilter (join-prefilter-obligations prefilter)))
         (jb1 (if (= count 5) (args-symbol a 2) (or (single-relation-name node0) "_1")))
         (jb2 (if (= count 5) (args-symbol a 3) (or (single-relation-name node1) "_2")))
         (jequi-left (and (member count '(3 5))
                          (nth-value 0 (try-extract-equi-keys (args-node a (if (= count 5) 4 2)) jb1 jb2))))
         (right-side nil)
         (owned-by-left (lambda (fields)
                          (notany (lambda (f) (or (gethash f (join-side-keys right-side)) (gethash f above-keys)))
                                  fields))))
    ;; With conjuncts to pre-apply and a left source that is itself a join,
    ;; the right source is evaluated first -- unobservable when both sources
    ;; are pure -- so the conjuncts still askable of the rows below travel down
    ;; to the join below, and from there to the base rows.
    (when (and deep stages jequi-left (eq (node-kind node0) :call)
               (member (node-s node0) '("LINK" "LINK_LEFT" "FILTER") :test #'string=)
               (join-pure-source-p node0) (join-pure-source-p node1))
      (let ((rv (args-val a 1)))
        (setf right-side (make-join-side-of rv (join-row-keys rv b2-names) is-left b2-names))
        ;; Whether the conjuncts the optimiser pushed below held so far: a
        ;; tentative FILTER on this join's right side has just run.
        (multiple-value-bind (applied stop)
            (join-stage-walk stages owned-by-left
                             (lambda (reqs) (join-totality-p reqs nil right-side above))
                             (= (context-tentative-kept ctx) kept-before))
          (declare (ignore applied))
          (let ((handed (join-truncate-stages stages stop)))
            (when handed
              ;; This join computes its left key on every row it receives; a
              ;; row dropped below never arrives, so the key goes down as an
              ;; obligation for the join that drops to prove.
              (setf (context-join-prefilter ctx)
                    (make-join-prefilter
                     :stages handed :deep t :above (cons right-side above)
                     :obligations (cons (make-join-obligation
                                         :key jequi-left
                                         :row-names (list jb1 (string-downcase jb1) "_1" "_")
                                         :outer (1+ (length above)))
                                        obligations))))))
        (unwind-protect (args-val a 0)
          (setf (context-join-prefilter ctx) nil))))
    (do-link-rows a ctx is-left prefilter stages deep above above-keys b1-names b2-names
                  kept-before right-side obligations)))

(defun do-link-rows (a ctx is-left prefilter stages deep above above-keys b1-names b2-names
                     kept-before right-side obligations)
  (let* ((owned-by-left (lambda (fields)
                          (notany (lambda (f) (or (gethash f (join-side-keys right-side)) (gethash f above-keys)))
                                  fields)))
         (count (args-count a))
         (val1 (args-val a 0))
         (val2 (args-val a 1))
         (pushed-held (= (context-tentative-kept ctx) kept-before))
         ;; The join below, if it applied some of these conjuncts, says which
         ;; ones every row that came up has passed; those are skipped here
         ;; unless a row was kept on an error below.
         (below (prog1 (context-join-prefilter-report ctx) (setf (context-join-prefilter-report ctx) nil)))
         (applied-below (and below (not (join-report-errored below)) (join-report-applied below)))
         (b1 "_1")
         (b2 "_2")
         pred-node)
    (cond
      ((= count 3)
       (let ((node0 (args-node a 0))
             (node1 (args-node a 1)))
         (let ((name0 (single-relation-name node0))
               (name1 (single-relation-name node1)))
           (when name0 (setf b1 name0))
           (when name1 (setf b2 name1))))
       (setf pred-node (args-node a 2)))
      ((= count 5)
       (setf b1 (args-symbol a 2)
             b2 (args-symbol a 3)
             pred-node (args-node a 4)))
      (t
       (fail "E_ARITY" (format nil "~a takes 3 or 5 arguments, got ~d" (args-name a) count)
             (args-pos-of a 0))))
    ;; Spec §7.4 "How a LINK evaluates": with no right elements PRED is never
    ;; evaluated. A LINK over an empty side is the empty list; a LINK_LEFT
    ;; with left rows but no right rows emits each unmatched row below
    ;; without touching PRED (the nested loop has no pairs to run it on).
    (let* ((first-r1 (unless (value-null-p val1) (first-collection-item val1)))
           (first-r2 (unless (value-null-p val1) (first-collection-item val2))))
      (if (or (value-null-p val1)
              (and (or (null first-r1) (null first-r2))
                   (or (not is-left) (null first-r1))))
        (make-list-value nil)
        ;; Every row is built from its own pair (spec §7.4): nothing is decided
        ;; from a first element except the shape of LINK_LEFT's null record.
        (let* ((sample-r2 (when first-r2 (ensure-row-table-alias first-r2 b2)))
               (null-r2 (when is-left
                          (let ((rec (make-null-record sample-r2 b2)))
                            (unless (value-null-p rec) rec))))
               (out '()))
          (let* ((facts (list nil))
                 (projector (make-join-projector b1 b2 null-r2 facts)))
            (multiple-value-bind (left-expr right-expr is-numeric)
                (try-extract-equi-keys pred-node b1 b2)
              (if (and left-expr right-expr sample-r2)
                  ;; --- HASH JOIN --- (only with right rows: the probe phase
                  ;; evaluates the left key per left row, and with no pairs
                  ;; PRED must not run at all)
                  (let ((ht (make-hash-table :test #'equal))
                        (b2-cell (cons b2 nil))
                        (b2-low-cell (cons (string-downcase b2) nil))
                        (b2-2-cell (cons "_2" nil)))
                    ;; Build phase on right relation with reusable frame. The
                    ;; rows are read in order here, where they are close
                    ;; together, rather than scattered pair by pair in the
                    ;; projector.
                    (let ((frame2 (list b2-cell b2-low-cell b2-2-cell))
                          (flat-row-p (make-join-flat-test
                                       (append (join-binder-keys b1 "_1") (join-binder-keys b2 "_2"))))
                          (flat t))
                      (ctx-push-frame ctx frame2)
                      (unwind-protect
                           (for-each-collection-item (item2 val2)
                             (let ((r2 (ensure-row-table-alias item2 b2)))
                               (when (and flat (not (funcall flat-row-p r2)))
                                 (setf flat nil))
                               (setf (cdr b2-cell) r2
                                     (cdr b2-low-cell) r2
                                     (cdr b2-2-cell) r2)
                               (let* ((key-val (args-eval a right-expr))
                                      (key (extract-join-key key-val is-numeric)))
                                 (when key
                                   (push r2 (gethash key ht))))))
                        (ctx-pop-frame ctx))
                      (setf (car facts) flat))
                    ;; The pre-filter, decided from the rows themselves
                    ;; (JOIN-STAGE-WALK). On a left row a conjunct evaluates
                    ;; FALSE the row is dropped -- the joined rows it would
                    ;; have produced would all have been dropped by the same
                    ;; conjunct. On an error the row is KEPT: the full
                    ;; predicate runs over the joined rows afterwards and
                    ;; raises there, in row order.
                    (let ((prefix '())
                          (binders '())
                          (report (make-join-report :applied (make-hash-table :test #'eq))))
                      (when prefilter
                        (unless right-side
                          (setf right-side (make-join-side-of val2 (join-row-keys val2 b2-names) is-left b2-names)))
                        (setf binders (mapcar #'car stages))
                        (let* ((left-side (make-join-side-of val1 (join-row-keys val1 b1-names) nil b1-names))
                               (self-names (list (string-upcase b1) "_1"))
                               ;; A joined row carries a left element's field
                               ;; exactly as the element does whenever no right
                               ;; element has the name (§7.4, pair by pair) --
                               ;; no row depends on another.
                               (owned-here owned-by-left)
                               ;; A handed-down join key that could raise on a
                               ;; dropped row, and nothing is dropped.
                               (safe (or (null obligations)
                                         (join-keys-safe-p obligations left-side right-side above))))
                          (dolist (c (and safe
                                          (join-stage-walk stages owned-here
                                                           (lambda (reqs) (join-totality-p reqs left-side right-side above))
                                                           pushed-held)))
                            (setf (gethash (jconj-node c) (join-report-applied report)) t)
                            (unless (and applied-below (gethash (jconj-node c) applied-below))
                              (push (if (some (lambda (f) (and (member f self-names :test #'string=)
                                                               (not (gethash f (join-side-first left-side)))))
                                              (jconj-fields c))
                                        (join-read-self (jconj-node c) self-names (jconj-binder c))
                                        (jconj-node c))
                                    prefix)))
                          (setf prefix (nreverse prefix))))
                      ;; A FILTER keeps its input's keys, so the rows dropped
                      ;; here still count towards the numbering of the rows
                      ;; kept, unless nothing observes it (DEEP). When the join
                      ;; key is a literal field of the row, a row that HAS it
                      ;; may be rejected before its key is computed.
                      (let* ((numbered (and prefix (not deep)))
                             (dropped nil)
                             (position 1)
                             (keyed '())
                             (fast-field (and prefix deep (eq (node-kind left-expr) :index)
                                              (node-l left-expr) (eq (node-kind (node-l left-expr)) :var)
                                              (node-r left-expr) (eq (node-kind (node-r left-expr)) :text)
                                              (member (string-upcase (node-s (node-l left-expr)))
                                                      (list (string-upcase b1) "_1" "_") :test #'string=)
                                              (node-s (node-r left-expr))))
                             (b1-cell (cons b1 nil))
                             (b1-low-cell (cons (string-downcase b1) nil))
                             (b1-1-cell (cons "_1" nil))
                             (b1-_-cell (cons "_" nil))
                             (frame1 (list b1-cell b1-low-cell b1-1-cell b1-_-cell))
                             (binder-cells (loop for binder in (remove-duplicates binders :test #'string=)
                                                 collect (or (find binder frame1 :key #'car :test #'string=)
                                                             (let ((cell (cons binder nil)))
                                                               (setf frame1 (append frame1 (list cell)))
                                                               cell)))))
                        (flet ((rejects (row)
                                 (dolist (cell binder-cells) (setf (cdr cell) row))
                                 (dolist (conjunct prefix nil)
                                   (let ((keep (handler-case (as-bool (args-eval a conjunct) (node-pos conjunct))
                                                 (sel-error ()
                                                   (setf (join-report-errored report) t)
                                                   (return nil)))))
                                     (unless keep (return t)))))
                               (emit (joined)
                                 (if numbered
                                     (push (cons (format nil "~d" position) joined) keyed)
                                     (push joined out))
                                 (incf position)))
                          (ctx-push-frame ctx frame1)
                          (unwind-protect
                               (for-each-collection-item (item1 val1)
                                 (block row
                                   (let ((r1 (ensure-row-table-alias item1 b1))
                                         (asked nil))
                                     (when (and fast-field (value-get r1 fast-field))
                                       (setf asked t)
                                       (when (rejects r1) (return-from row)))
                                     (setf (cdr b1-cell) r1
                                           (cdr b1-low-cell) r1
                                           (cdr b1-1-cell) r1
                                           (cdr b1-_-cell) r1)
                                     (let* ((key-val (args-eval a left-expr))
                                            (key (extract-join-key key-val is-numeric))
                                            (matches (and key (gethash key ht))))
                                       (when (and prefix (not asked) (rejects r1))
                                         (setf dropped t)
                                         (when numbered
                                           (incf position (cond (matches (length matches)) (is-left 1) (t 0))))
                                         (return-from row))
                                       (if matches
                                           (dolist (r2 (reverse matches))
                                             (emit (funcall projector r1 r2)))
                                           (when is-left
                                             (emit (funcall projector r1 nil))))))))
                            (ctx-pop-frame ctx)))
                        (when prefilter (setf (context-join-prefilter-report ctx) report))
                        (when numbered
                          (if (and dropped keyed)
                              (return-from do-link-rows
                                (%value-with-children :none nil (nreverse keyed) t))
                              (setf out (append (mapcar #'cdr keyed) out)))))))
                  ;; --- NESTED LOOP FALLBACK ---
                  (let ((b1-cell (cons b1 nil))
                        (b1-low-cell (cons (string-downcase b1) nil))
                        (b1-1-cell (cons "_1" nil))
                        (b1-_-cell (cons "_" nil))
                        (b2-cell (cons b2 nil))
                        (b2-low-cell (cons (string-downcase b2) nil))
                        (b2-2-cell (cons "_2" nil)))
                    (let ((frame (list b1-cell b1-low-cell b1-1-cell b1-_-cell
                                       b2-cell b2-low-cell b2-2-cell)))
                      (ctx-push-frame ctx frame)
                      (unwind-protect
                           (for-each-collection-item (item1 val1)
                             (let ((r1 (ensure-row-table-alias item1 b1))
                                   (matched nil))
                               (setf (cdr b1-cell) r1 (cdr b1-low-cell) r1
                                     (cdr b1-1-cell) r1 (cdr b1-_-cell) r1)
                               (when sample-r2
                                 (for-each-collection-item (item2 val2)
                                   (let ((r2 (ensure-row-table-alias item2 b2)))
                                     (setf (cdr b2-cell) r2 (cdr b2-low-cell) r2 (cdr b2-2-cell) r2)
                                     (when (as-bool (args-eval a pred-node) (node-pos pred-node))
                                       (setf matched t)
                                       (push (funcall projector r1 r2) out)))))
                               (when (and is-left (not matched))
                                 (push (funcall projector r1 nil) out))))
                        (ctx-pop-frame ctx))))))
            (make-list-value (nreverse out))))))))

;; Three or five arguments, refused at compile time like every E_ARITY (spec
;; §7.4); the rule is spec/builtins.json's and DEFINE-BUILTIN installs it.
(define-builtin "LINK" 3 5
  (lambda (a ctx) (do-link a ctx nil))
  :lazy t :binds t)

(define-builtin "LINK_LEFT" 3 5
  (lambda (a ctx) (do-link a ctx t))
  :lazy t :binds t)

