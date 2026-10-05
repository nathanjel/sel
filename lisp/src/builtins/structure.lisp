(in-package #:sel)

;;; Binder and table names compare ASCII-case-insensitively (spec §2, §7.4):
;;; this file folds them with ASCII-DOWNCASE and ASCII-EQUAL (utf8.lisp), never
;;; CL's STRING-DOWNCASE / STRING-EQUAL, which SBCL applies to every 1:1 Unicode
;;; case pair -- "é" and "É" once collided in joined rows.

;;; A LINK side's row is bound under its name, the name's ASCII lowercase and
;;; its positional names (spec §7.4): "_1" and "_" on the left, "_2" on the
;;; right. WITH-ROW-BINDER makes those cells once -- FRAME is the list to push
;;; -- and (SET row) stores a row in every one of them, unrolled, since it runs
;;; once per row.
(defmacro with-row-binder ((frame set) name positionals &body body)
  (let ((cells (loop repeat (+ 2 (length positionals)) collect (gensym "CELL")))
        (n (gensym "NAME")))
    `(let* ((,n ,name)
            (,(first cells) (cons ,n nil))
            (,(second cells) (cons (ascii-downcase ,n) nil))
            ,@(loop for c in (cddr cells) for pos in positionals collect `(,c (cons ,pos nil)))
            (,frame (list ,@cells)))
       (declare (ignorable ,frame))
       (macrolet ((,set (row)
                    (list 'let (list (list '%row row))
                          (cons 'setf (loop for c in ',cells append (list (list 'cdr c) '%row))))))
         ,@body))))

;;; A scalar with no children behaves as a one-element list containing itself,
;;; consistent with scalar context (§3.2). A NONE with no children is genuinely
;;; empty — that is what FILTER returns when nothing matched, and ALL over it
;;; must be TRUE rather than a scalar-context failure.
(defun aggregate-elements (v)
  (cond ((plusp (value-size v)) (%value-entries v))
        ((eq (value-kind v) :none) '())
        (t (list (cons "1" v)))))

(define-builtin "COUNT" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (value-size (args-val a 0)))))

(define-builtin "INDEXES" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value (mapcar #'%text (%value-keys (args-val a 0))))))

(define-builtin "HAS" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-bool (value-has (args-val a 0) (args-text a 1)))))

(define-builtin "LIST" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    ;; More than MAX_COLLECTION arguments is E_RANGE at the call (spec §6.4),
    ;; before any is evaluated, as `,` refuses.
    (check-collection-cap (args-count a) (args-pos a))
    (make-list-value
     (loop for i below (args-count a)
           collect (value-copy-at (args-val a i) 2 (args-pos a))))))

(define-builtin "RECORD" 0 +variadic+
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((n (args-count a))
           (num-fields (ash n -1)))
      ;; More than MAX_COLLECTION pairs is E_RANGE at the call (spec §6.4).
      (check-collection-cap num-fields (args-pos a))
      (if (zerop n)
          (make-none)
          (let* ((keys (loop for i from 0 below n by 2 collect (args-text a i)))
                 (prepared (args-record-shape a))
                 (matches (and prepared (equal keys (record-shape-keys prepared)))))
            (if (or matches (keys-distinct-p keys))
                (let* ((shape (if matches prepared (get-record-shape keys)))
                       (storage (make-array num-fields)))
                  (loop for i from 0 below n by 2
                        for slot-idx from 0
                        do (setf (svref storage slot-idx) (value-copy-at (args-val a (1+ i)) 2 (args-pos a))))
                  (%make-shaped-value shape storage))
                (let ((rec (make-none)))
                  (loop for i from 0 below n by 2
                        do (value-set rec (args-text a i) (value-copy-at (args-val a (1+ i)) 2 (args-pos a))))
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

(defun collection-items (v)
  "The elements of the collection V as a fresh simple-vector: a SNAPSHOT of the
references (spec 7.3), so a body or predicate that appends to, adds to or replaces
V afterwards neither extends the walk nor moves what it stands on."
  (let ((vec (value-element-vector v)))
    (if vec
        (copy-seq vec)
        (coerce (mapcar #'cdr (aggregate-elements v)) 'simple-vector))))

(defmacro for-each-collection-item ((item-var coll) &body body)
  `(loop for ,item-var across (collection-items ,coll)
         do (progn ,@body)))

(defun first-collection-item (v)
  "V's first element: the first child of a collection, a scalar itself, NIL
when there is none. Read straight from a list's or shaped record's storage;
only an alist record walks its entries."
  (cond
    ((and (eq (value-kind v) :none) (zerop (value-size v)))
     nil)
    ((let ((vec (value-element-vector v)))
       (and vec (plusp (length vec)) (svref vec 0))))
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
                    (unless (member (node-s n) allowed-binders :test #'ascii-equal)
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
                   ;; A `,` list, a `;` sequence and an assignment read whatever
                   ;; their parts read -- an assignment's target included, which it
                   ;; reads as a variable: skipping them let an operand over BOTH
                   ;; binders pass for one-sided.
                   ((:list :seq)
                    (dolist (item (node-items n)) (walk item)))
                   (:assign
                    (walk (node-l n))
                    (walk (node-r n)))))))
      (walk node)
      all-ok)))

(defun try-extract-equi-keys (pred-node b1 b2)
  "Checks if PRED-NODE is an equality comparison between an expression on B1 and an expression on B2.
Returns (values left-expr right-expr is-numeric swapped) or NIL; SWAPPED is true
when the operator's left operand reads the right side."
  ;; Two binders of one name are one name: the right shadows the left (SPEC 7.4),
  ;; so an operand written over it reads the right element only, and no key can be
  ;; taken from the left row. The general path answers.
  (when (and pred-node
             (node-p pred-node)
             (eq (node-kind pred-node) :bin)
             ;; `==` or `$==`: the equality of either comparison family.
             (eq (op-relation (node-s pred-node)) :eq)
             (not (ascii-equal b1 b2)))
    (let ((l (node-l pred-node))
          (r (node-r pred-node))
          (is-numeric (compare-op-p (node-s pred-node)))
          (b1-names (list b1 (ascii-downcase b1) "_1" "_"))
          (b2-names (list b2 (ascii-downcase b2) "_2")))
      (cond
        ((and (expr-depends-only-on l b1-names)
              (expr-depends-only-on r b2-names))
         (values l r is-numeric nil))
        ((and (expr-depends-only-on r b1-names)
              (expr-depends-only-on l b2-names))
         (values r l is-numeric t))
        (t nil)))))

;; One key per number, as `==` compares it (spec §7.4): trailing fraction
;; zeros and a negative zero are representation, not value. The plain-integer
;; shortcut is an ASCII check, never DIGIT-CHAR-P, which accepts other scripts.
(defun canonical-dec-string (d)
  "The canonical spelling of the parsed number D: no trailing fraction zeros, no
negative zero. Trailing zeros are dropped from the FORMATTED text, because
stripping them from the integer by `(floor digits 10)` was a bignum division per
zero -- quadratic in the zeros, 20 s for a key with 100,000 of them."
  (if (zerop (dec-digits d))
      "0"
      (let ((text (dec-format d)))
        (if (plusp (dec-scale d))
            (let ((end (length text)))
              (loop while (char= (char text (1- end)) #\0) do (decf end))
              ;; Only fraction zeros can have been dropped: scale > 0 puts a dot
              ;; before them, and the loop stops at it.
              (when (char= (char text (1- end)) #\.) (decf end))
              (subseq text 0 end))
            text))))

(defun canonical-numeric-string (sc)
  (declare (type string sc))
  (let ((len (length sc)))
    ;; The shortcut is for numbers the digit cap allows: past it the text is not a
    ;; number at all, and `==` raises E_RANGE on it (SPEC 6.4), so the key must not
    ;; pass for a plain integer.
    (if (and (plusp len)
             (<= len +max-int-digits+)
             (or (char/= (char sc 0) #\0) (= len 1))
             (loop for i from 0 below len
                   always (char<= #\0 (char sc i) #\9)))
        sc
        (let ((d (dec-parse sc)))
          (when d (canonical-dec-string d))))))

;; `_1` and `_2` name a position, not a relation: an argument with no name is
;; bound bare (spec §7.4).
(defun positional-binder-p (name)
  (or (string= name "_1") (string= name "_2")))

;; An equi-join key: the value as the comparison would compare it (spec §7.4)
;; -- `==` through AS-DEC, `$==` through AS-BYTES, the coercions the evaluator
;; uses -- so a BIN meets the TEXT of its bytes and a list its scalar. NIL for
;; NULL (never compared); (:BAD . value) for a value the coercion rejects, whose
;; pair must raise as the comparison would.
(defun extract-join-key (val is-numeric)
  (when (and val (not (value-null-p val)))
    (handler-case
        (if is-numeric
            (let ((sc (and (eq (value-kind val) :text) (value-scalar val))))
              (or (and (stringp sc) (canonical-numeric-string sc))
                  (canonical-dec-string (as-dec val nil))))
            (let ((bytes (as-bytes val nil)))
              (map 'string #'code-char bytes)))
      (sel-error () (cons :bad val)))))

(defun join-key-bad-p (key) (and (consp key) (eq (car key) :bad)))

;; What the left keys are checked against: whether any right key is live (not
;; NULL), the first live one if it was rejected, and the first rejected one.
(defstruct (join-right-facts (:conc-name jrf-)) (live nil) (live-bad nil) (bad nil))

(defun note-right-join-key (facts key)
  (when key
    (when (join-key-bad-p key)
      (unless (jrf-live facts) (setf (jrf-live-bad facts) (cdr key)))
      (unless (jrf-bad facts) (setf (jrf-bad facts) (cdr key))))
    (setf (jrf-live facts) t)))

(defun coerce-join-operand (is-numeric value node)
  (if is-numeric (as-dec value (node-pos node)) (as-bytes value (node-pos node)))
  (error "a rejected join key did not raise"))

;; A left key meets the right keys pair by pair, in order, as the comparison
;; would: a rejected left key raises against the first live right key, a good
;; one against the first rejected right key -- the operator's left operand
;; coerced first. NULLs are never compared.
(defun check-join-pair (key facts left-expr right-expr is-numeric swapped)
  (when (and key (jrf-live facts))
    (when (join-key-bad-p key)
      (when (and swapped (jrf-live-bad facts))
        (coerce-join-operand is-numeric (jrf-live-bad facts) right-expr))
      (coerce-join-operand is-numeric (cdr key) left-expr))
    (when (jrf-bad facts)
      (coerce-join-operand is-numeric (jrf-bad facts) right-expr))))

;;; LINK with `key-equality AND residual...`. The predicate is not a bare
;;; `==`, so the hash join above does not apply, and the nested loop ran the
;;; evaluator on every (left, right) pair: n*m. When the LEADING conjunct is an
;;; equality between an expression over the left element and one over the right,
;;; a pair whose keys differ has a FALSE first conjunct, so the residual conjuncts
;;; (and their errors and effects) never run for it: only the pairs whose keys are
;;; equal need the predicate. Those are found by hashing, and the predicate then
;;; runs on each, unchanged, in the order the nested loop would reach them.
;;;
;;; Used only where that is provably the same program: the predicate has no
;;; assignment and calls only shipped built-ins (nothing in it can change what a
;;; later pair reads), and every key of both sides is a live, good key -- computed
;;; up front, with any failure (an error, a NULL, a rejected key) sending the join
;;; to the nested loop, which raises it where and as it always did.
(defun leading-and-conjunct (pred)
  "The first conjunct of a left-nested AND chain, or NIL when PRED is not one."
  (when (and pred (node-p pred) (eq (node-kind pred) :bin) (string= (node-s pred) "AND"))
    (let ((n pred))
      (loop while (and (node-p n) (eq (node-kind n) :bin) (string= (node-s n) "AND"))
            do (setf n (node-l n)))
      n)))

(defun link-pred-pure-p (node)
  "No assignment anywhere in NODE, and every call is to a shipped built-in."
  (labels ((pure (n)
             (or (null n) (not (node-p n))
                 (and (case (node-kind n)
                        (:assign nil)
                        (:call (shipped-call-p n))
                        (t t))
                      (pure (node-l n))
                      (pure (node-r n))
                      (every #'pure (node-items n))))))
    (pure node)))

(defun and-residual-candidates (a ctx pred b1 b2 items1 items2)
  "(values left-keys table) -- the canonical key of every left element, and the
indexes of the right elements under each key in right order -- or NIL when the
join is not of this shape or any key is not a live, good one."
  (let ((lead (leading-and-conjunct pred)))
    (when (and lead (link-pred-pure-p pred))
      (multiple-value-bind (left-expr right-expr is-numeric) (try-extract-equi-keys lead b1 b2)
        (when (and left-expr right-expr)
          (handler-case
              (let ((table (make-hash-table :test #'equal))
                    (left-keys (make-array (length items1))))
               (with-row-binder (frame1 set-left) b1 ("_1" "_")
               (with-row-binder (frame2 set-right) b2 ("_2")
                (flet ((live-key (expr)
                         (let ((key (extract-join-key (args-eval a expr) is-numeric)))
                           (when (or (null key) (join-key-bad-p key))
                             (return-from and-residual-candidates nil))
                           key)))
                  (ctx-push-frame ctx frame2)
                  (unwind-protect
                       (loop for item2 across items2 for j from 0
                             do (let ((r2 (ensure-row-table-alias item2 b2)))
                                  (set-right r2)
                                  (push j (gethash (live-key right-expr) table))))
                    (ctx-pop-frame ctx))
                  (maphash (lambda (k v) (setf (gethash k table) (nreverse v))) table)
                  (ctx-push-frame ctx frame1)
                  (unwind-protect
                       (loop for item1 across items1 for i from 0
                             do (let ((r1 (ensure-row-table-alias item1 b1)))
                                  (set-left r1)
                                  (setf (svref left-keys i) (live-key left-expr))))
                    (ctx-pop-frame ctx)))))
                (values left-keys table))
            (sel-error () nil)))))))

(defun make-null-record (sample-row tbl-name)
  "An unmatched LINK_LEFT row's right side (spec §7.4): shaped like the first
right element as bound -- SAMPLE-ROW, already extended with the name -- every
field NULL; with no right elements, just the name keys; with no name either,
NULL. VALUE-SET on a key that exists keeps its place."
  (let ((null-rec (make-none)))
    (when sample-row
      (dolist (k (%value-keys sample-row))
        (value-set null-rec k (make-none))))
    (when (and (null sample-row) tbl-name (not (positional-binder-p tbl-name)))
      (let ((low (ascii-downcase tbl-name)))
        (unless (value-has null-rec tbl-name) (value-set null-rec tbl-name (make-none)))
        (when (and (string/= tbl-name low) (not (value-has null-rec low)))
          (value-set null-rec low (make-none)))))
    null-rec))

;; Keep ownership global, not on record shapes: source/destination chains must
;; remain bounded. Two lookup levels avoid a fresh composite key on every hit
;; while preserving multiple table names per source shape.
;;
;; Spec §8.1 lets several threads run programs at once, and this cache is read
;; once per joined row, so readers take no lock: both levels are copy-on-write.
;; A table is never written once it is reachable from *ALIAS-PLAN-CACHE*; a
;; writer, holding *ALIAS-PLAN-LOCK*, builds a new inner and outer table and
;; publishes the outer one with a single store. SBCL allows any number of
;; concurrent readers on an unsynchronised table that nobody writes.
(defparameter *alias-plan-cache* (make-hash-table :test #'eq))
(defparameter *alias-plan-cache-count* 0)
(defvar *alias-plan-lock* (sb-thread:make-mutex :name "sel alias plan cache"))

(defun copy-hash-table-adding (table test key value)
  (let ((new (make-hash-table :test test :size (1+ (if table (hash-table-count table) 0)))))
    (when table
      (maphash (lambda (k v) (setf (gethash k new) v)) table))
    (setf (gethash key new) value)
    new))

(defun publish-alias-plan (old-shape tbl-name plan)
  "Add PLAN for (OLD-SHAPE, TBL-NAME), starting over once the cache holds
+SHAPE-CACHE-ENTRIES+ plans."
  (sb-thread:with-mutex (*alias-plan-lock*)
    (let ((cache *alias-plan-cache*))
      (when (>= *alias-plan-cache-count* +shape-cache-entries+)
        (setf cache nil *alias-plan-cache-count* 0))
      (let* ((plans (and cache (gethash old-shape cache)))
             (fresh (not (and plans (gethash tbl-name plans))))
             (new-plans (copy-hash-table-adding plans #'equal tbl-name plan))
             (new-cache (copy-hash-table-adding cache #'eq old-shape new-plans)))
        (sb-thread:barrier (:write))
        (setf *alias-plan-cache* new-cache)
        (when fresh (incf *alias-plan-cache-count*))))))

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
                   (let* ((low (ascii-downcase tbl-name))
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
                       (publish-alias-plan old-shape tbl-name (list ns diff olen)))
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
         (let* ((low (ascii-downcase tbl-name))
                (diff (and (string/= tbl-name low) (not (value-has row low))))
                (extra (if diff
                           (list (cons tbl-name row) (cons low row))
                           (list (cons tbl-name row))))
                (new-children (append (value-children row) extra)))
           ;; The extended element is a record of the element's fields and the
           ;; name keys: no scalar of its own, whatever the element's kind, and
           ;; not a list once it has a name key (spec §7.4).
           (%value-with-children :none nil new-children nil))))))

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
  (let ((low (ascii-downcase name)))
    (append (list name)
            (when (string/= low name) (list low))
            (when (string/= name positional) (list positional)))))

(defun join-entries (v)
  (if (and v (plusp (value-size v)) (not (value-is-list v)))
      (loop for k in (%value-keys v) collect (cons k (value-get v k)))
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
        (let ((right-names (mapcar (lambda (e) (ascii-upcase (car e))) right-entries)))
          (loop for (k . v) in left-entries
                unless (or (= (join-category v) +join-nested+)
                           (member (ascii-upcase k) right-names :test #'string=))
                  do (put k v)))
        (when r2
          (let ((left-names (mapcar (lambda (e) (ascii-upcase (car e))) left-entries)))
            (loop for (k . v) in right-entries
                  when (and (= (join-category v) +join-scalar+)
                            (not (member (ascii-upcase k) left-names :test #'string=)))
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
        (let ((right-names (mapcar #'ascii-upcase rkeys)))
          (loop for k in lkeys for i from 0
                unless (or (= (join-category (svref lstore i)) +join-nested+)
                           (member (ascii-upcase k) right-names :test #'string=))
                  do (put k (cons :l i))))
        (when matched
          (let ((left-names (mapcar #'ascii-upcase lkeys)))
            (loop for k in rkeys for i from 0
                  when (and (= (join-category (svref rstore i)) +join-scalar+)
                            (not (member (ascii-upcase k) left-names :test #'string=)))
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
      (let ((left-names (mapcar #'ascii-upcase (record-shape-keys (value-shape r1))))
            (binder-names (append (join-binder-keys b1 "_1") (join-binder-keys b2 "_2")))
            (rs (value-storage rside)))
        (loop for k in (record-shape-keys (value-shape rside)) for i from 0
              for v = (svref rs i)
              when (and (not (member (ascii-upcase k) left-names :test #'string=))
                        (not (member k binder-names :test #'string=))
                        (eq (value-kind v) :none) (not (value-is-list v)))
                do (push i rkept))))
    (%make-join-plan :shape (car plan) :ops ops :slots slots
                     :lrest lrest :rkept (nreverse rkept))))

(defun join-plan-build (plan r1 rside check-left check-right &optional (o1 r1) (o2 rside))
  "The row PLAN makes of R1 and RSIDE (the shaped views the fields are read
from; O1 and O2 are the elements themselves, which the binder keys hold), or NIL
when the pair breaks its assumptions: with CHECK-LEFT, that each left field is (or is not) a nested
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
                (2 o1)
                (t o2))))
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
        (twins (make-hash-table :test #'eq))
        (last-left nil) (last-plan nil) (last-list nil)
        (pair-l nil) (pair-r nil) (pair-matched nil))
    (flet ((shaped-view (row)
             ;; A row with no record shape (built by assignment, say, and not from
             ;; data the host converted) is read through a shaped twin made once:
             ;; the same keys in the same order holding the same values. Without
             ;; it every pair of such rows took MAKE-JOINED-ROW, which builds two
             ;; hash tables and several lists per pair (3-6x slower).
             (cond ((null row) nil)
                   ((and (value-shape row) (value-storage row)) row)
                   (t (multiple-value-bind (twin found) (gethash row twins)
                        (if found
                            twin
                            (setf (gethash row twins)
                                  (and (plusp (value-size row)) (not (value-is-list row))
                                       (let* ((keys (%value-keys row))
                                              (storage (make-array (length keys))))
                                         (loop for k in keys for i from 0
                                               do (setf (svref storage i) (value-get row k)))
                                         (%make-shaped-value (get-record-shape keys) storage))))))))))
    (lambda (r1 r2)
      (block project
        (let* ((o1 r1)
               (o2 (or r2 null-r2))
               (s1 (shaped-view o1))
               (s2 (shaped-view o2))
               (r1 s1)
               (rside s2))
          (if (or (null r1) (null rside))
              (make-joined-row o1 r2 b1 b2 null-r2)
              (let ((lshape (value-shape r1))
                    (rshape (value-shape rside))
                    (matched (and r2 t))
                    (list nil))
                (flet ((remember (plan row)
                         (setf last-left o1 last-plan plan last-list list
                               pair-l lshape pair-r rshape pair-matched matched)
                         row))
                  ;; The same shapes as the last pair: its plan first, checking
                  ;; the left row only when it is a new one.
                  (if (and last-plan (eq lshape pair-l) (eq rshape pair-r) (eq matched pair-matched))
                      (let ((row (join-plan-build last-plan r1 rside (not (eq last-left o1)) (not (car facts))
                                                  o1 o2)))
                        (when row
                          (setf last-left o1)
                          (return-from project row))
                        (setf list last-list))
                      (let ((key (list lshape rshape matched)))
                        (setf list (or (gethash key plans)
                                       (setf (gethash key plans) (list :plans))))))
                  (dolist (plan (cdr list))
                    (let ((row (join-plan-build plan r1 rside t (not (car facts)) o1 o2)))
                      (when row (return-from project (remember plan row)))))
                  (let ((plan (compile-join-plan r1 rside matched b1 b2)))
                    (setf (cdr (last list)) (list plan))
                    (remember plan (join-plan-build plan r1 rside nil nil o1 o2))))))))))))

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
;;; of the AST. docs/contributing.md states the rule for every host.

(defstruct jconj
  node            ; a conjunct of the FILTER body, in the tree
  field-only      ; reads nothing but fields of the row
  fields          ; upper-cased names; `_["orders"]["x"]` reads ORDERS
  has-total       ; a comparison of literals and bare field reads
  total           ; ((field-as-written . numeric-p) ...)
  binder)         ; the FILTER's element name

;; stages: ((binder jconjs above) ...), ABOVE the count of joins between the
;; stage's FILTER and the join testing it; obligations: JOIN-OBLIGATIONs.
(defstruct join-prefilter stages deep above obligations)
;; applied: EQ hash table of nodes; dropped: whether any row was dropped.
(defstruct join-report applied errored dropped)
;; names: the member names the side's row is bound under (never `_1`/`_2`).
(defstruct join-side value keys first nullable names (facts (make-hash-table :test #'equal)))
;; A join's left key, handed down with its conjuncts: the join that drops
;; rows must prove it cannot raise on them (JOIN-KEYS-SAFE-P).
(defstruct join-obligation key row-names outer)


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
                                       (pushnew (ascii-upcase (node-s (node-r n))) fields :test #'string=)
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
                                     (relational-op-p (node-s c))))
                       (numeric (and compare (compare-op-p (node-s c)) t))
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
        (:call (and (shipped-call-p node)
                    (not (string= (node-s node) "ABORT"))
                    (every #'join-pure-source-p (node-items node))))
        (t nil))))

(defun join-stage-walk (stages owned-here total-here &optional right-here)
  "The JCONJs a join may test before it joins, in stage order, as (jconj stage
right-p) entries, and where the walk stopped as (stage-index .
conjunct-index), or NIL. One whose fields are all the left rows' is applied to
them; one that reads only through this join's right binder (RIGHT-HERE,
`_[\"products\"][\"is_active\"]`) is applied to the right rows; one reading a
right side's field ends the walk (AND short-circuits left to right) unless it
is total here, in which case it is passed over for the join above; one
reading anything else ends it too. Each stage is judged against the joins
between its FILTER and this join (its third element, the count of them): a
FILTER in the middle of a chain reads rows no join above it has touched."
  (let ((applied '()))
    (loop for stage in stages
          for si from 0
          do (loop for c in (second stage)
                   for ci from 0
                   do (cond ((and (jconj-field-only c) (funcall owned-here (jconj-fields c) stage))
                             (push (list c stage nil) applied))
                            ((and (jconj-field-only c) right-here (funcall right-here (jconj-fields c) stage))
                             (push (list c stage t) applied))
                            ((and (jconj-has-total c) (funcall total-here (jconj-total c) stage)))
                            (t (return-from join-stage-walk (values (nreverse applied) (cons si ci)))))))
    (values (nreverse applied) nil)))

(defun join-truncate-stages (stages stop)
  (if (null stop)
      stages
      (destructuring-bind (si . ci) stop
        (append (subseq stages 0 si)
                (when (plusp ci)
                  (let ((stage (nth si stages)))
                    (list (list (first stage) (subseq (second stage) 0 ci) (third stage)))))))))

(defun join-raw-safe-p (node binders avoid)
  "Whether NODE reads the element bound to BINDERS only as `r[\"f\"]` with F,
upper-cased, not AVOID: then it reads the same on the element as it arrives
and on the element extended with its relation's name (ENSURE-ROW-TABLE-ALIAS
adds only that name), and may run before the extension is made."
  (cond ((or (null node) (not (node-p node))) t)
        ((and (eq (node-kind node) :index) (node-l node) (eq (node-kind (node-l node)) :var)
              (member (node-s (node-l node)) binders :test #'string=))
         (and (node-r node) (eq (node-kind (node-r node)) :text)
              (string/= (ascii-upcase (node-s (node-r node))) avoid)))
        ((and (eq (node-kind node) :var) (member (node-s node) binders :test #'string=)) nil)
        (t (and (join-raw-safe-p (node-l node) binders avoid)
                (join-raw-safe-p (node-r node) binders avoid)
                (every (lambda (item) (join-raw-safe-p item binders avoid)) (node-items node))))))

(defun join-read-self (node names binder)
  "NODE with every `r[\"orders\"]` -- a read through the left binder's own
name (NAMES, upper-cased) on the element BINDER -- replaced by the element:
on the left rows themselves the joined row's member of that name is the row."
  (cond ((null node) nil)
        ((and (eq (node-kind node) :index) (node-l node) (eq (node-kind (node-l node)) :var)
              (string= (node-s (node-l node)) binder)
              (node-r node) (eq (node-kind (node-r node)) :text)
              (member (ascii-upcase (node-s (node-r node))) names :test #'string=))
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
    (dolist (b bound) (setf (gethash (ascii-upcase b) keys) t))
    (for-each-collection-item (row value)
      (let ((shape (value-shape row)))
        (if shape
            (unless (gethash shape shapes)
              (setf (gethash shape shapes) t)
              (dolist (k (record-shape-keys shape)) (setf (gethash (ascii-upcase k) keys) t)))
            (dolist (k (%value-keys row)) (setf (gethash (ascii-upcase k) keys) t)))))
    keys))

(defun make-join-side-of (value keys nullable &optional bound)
  (let ((first-keys (make-hash-table :test #'equal))
        (first (first-collection-item value)))
    (when first
      (dolist (k (%value-keys first)) (setf (gethash (ascii-upcase k) first-keys) t)))
    (make-join-side :value value :keys keys :first first-keys :nullable nullable
                    :names (loop for b in bound
                                 unless (member b '("_1" "_2" "_") :test #'string=)
                                   append (list b (ascii-downcase b))))))

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
  (and (gethash (ascii-upcase name) (join-side-first side)) (not (join-side-nullable side))
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
           (let ((owners (remove-if-not (lambda (sd) (gethash (ascii-upcase field) (join-side-keys sd)))
                                        (list* left right below))))
             (unless (and (= (length owners) 1) (join-side-any-p (first owners) field))
               (return nil))))
          (t (return nil)))))))

(defun join-side-total-p (side name numeric)
  "The field NAME (as written) is on SIDE's first row and on every row, as
text (a number is text) or, when NUMERIC, as a number."
  (when (and (gethash (ascii-upcase name) (join-side-first side)) (not (join-side-nullable side)))
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
        always (let* ((key (ascii-upcase name))
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
         ;; The upper-cased keys of the joins between a stage's FILTER and
         ;; this join, per count of them.
         (above-keys-cache (make-hash-table))
         (above-keys (lambda (stage)
                       (let ((n (third stage)))
                         (or (gethash n above-keys-cache)
                             (setf (gethash n above-keys-cache)
                                   (let ((h (make-hash-table :test #'equal)))
                                     (dolist (side (subseq above 0 (min n (length above))) h)
                                       (maphash (lambda (k v) (declare (ignore v)) (setf (gethash k h) t))
                                                (join-side-keys side)))))))))
         (node0 (args-node a 0))
         (node1 (args-node a 1))
         ;; The keys a side contributes to the joined row include the names
         ;; its row is bound under: `_["products"]` after LINK(PRODUCTS, ...)
         ;; is the right row, not a field of the left ones.
         ;; The names the sides' rows are bound under (spec §7.4), read once
         ;; here -- an explicit binder that is not a bare name is E_EXPECT_SYMBOL
         ;; before either source runs -- and handed down.
         (jb1 (if (= count 5) (args-symbol a 2) (or (single-relation-name node0) "_1")))
         (jb2 (if (= count 5) (args-symbol a 3) (or (single-relation-name node1) "_2")))
         (b1-names (list jb1 "_1"))
         (b2-names (list jb2 "_2"))
         (pred-node (args-node a (if (= count 5) 4 2)))
         (obligations (and prefilter (join-prefilter-obligations prefilter)))
         (jequi-left (nth-value 0 (try-extract-equi-keys pred-node jb1 jb2)))
         (right-side nil)
         (owned-by-left (lambda (fields stage)
                          (fields-owned-by-left-p fields stage above-keys right-side))))
    ;; With conjuncts to pre-apply and a left source that is itself a join,
    ;; the right source is evaluated first -- unobservable when both sources
    ;; are pure -- so the conjuncts still askable of the rows below travel down
    ;; to the join below, and from there to the base rows.
    (when (and deep stages jequi-left (eq (node-kind node0) :call)
               (member (node-s node0) '("LINK" "LINK_LEFT" "FILTER") :test #'string=)
               (join-pure-source-p node0) (join-pure-source-p node1))
      (let ((rv (handler-case (args-val a 1)
                  ;; The right source went first for the prefilter's sake, which is
                  ;; only unobservable while neither source raises (SPEC 7.4: as
                  ;; written, the left source runs first). The left source is pure
                  ;; too: run it now, after the failed evaluation has unwound and
                  ;; with nothing handed down. If it raises, ITS error is the one
                  ;; as written; if it does not, the right source's error stands.
                  (sel-error (c) (args-val a 0) (error c)))))
        (setf right-side (make-join-side-of rv (join-row-keys rv b2-names) is-left b2-names))
        (multiple-value-bind (applied stop)
            (join-stage-walk stages owned-by-left
                             (lambda (reqs stage)
                               (join-totality-p reqs nil right-side (subseq above 0 (min (third stage) (length above))))))
          (declare (ignore applied))
          ;; Below this join, every stage has one more join above it: this one.
          (let ((handed (mapcar (lambda (stage) (list (first stage) (second stage) (1+ (third stage))))
                                (join-truncate-stages stages stop))))
            (when handed
              ;; This join computes its left key on every row it receives; a
              ;; row dropped below never arrives, so the key goes down as an
              ;; obligation for the join that drops to prove.
              (setf (context-join-prefilter ctx)
                    (make-join-prefilter
                     :stages handed :deep t :above (cons right-side above)
                     :obligations (cons (make-join-obligation
                                         :key jequi-left
                                         :row-names (list jb1 (ascii-downcase jb1) "_1" "_")
                                         :outer (1+ (length above)))
                                        obligations))))))
        (unwind-protect (args-val a 0)
          (setf (context-join-prefilter ctx) nil))))
    ;; However this join ends, an error caught above it (`??`) must not leave a
    ;; prefilter or a report in the context for an unrelated join later in the
    ;; same run to pick up: they are consumed by the join they were meant for, and
    ;; only a normal return proves that one was (a hardening rule).
    (let ((completed nil))
      (unwind-protect
           (multiple-value-prog1
               (do-link-rows a ctx is-left prefilter stages deep above above-keys b1-names b2-names
                             right-side obligations jb1 jb2 pred-node)
             (setf completed t))
        (unless completed
          (setf (context-join-prefilter ctx) nil
                (context-join-prefilter-report ctx) nil))))))

(defun fields-owned-by-left-p (fields stage above-keys right-side)
  "Whether none of FIELDS is a key the right side, or a join above STAGE,
contributes: a conjunct reading only such fields is the left rows' own."
  (let ((upper (funcall above-keys stage)))
    (notany (lambda (f) (or (gethash f (join-side-keys right-side)) (gethash f upper)))
            fields)))

;;; DO-LINK-ROWS is split by phase, each phase once per LINK call: the sources
;;; (LINK-SOURCES), then either the hash join -- its build (LINK-HASH-BUCKETS),
;;; the pre-filter's plan (LINK-PREFILTER-PLAN) and the probe -- or the nested
;;; loop. The per-row loops stay whole inside LINK-HASH-JOIN and
;;; LINK-NESTED-LOOP, with the local functions they call per row, so the split
;;; adds no call per row.

(defun link-sources (a ctx)
  "(values val1 val2 below applied-below): the two sources, as written or as
the prefilter left them, and the report of the join below."
  (let* ((given1 (args-val a 0))
         (val2 (args-val a 1))
         ;; The join below, if it applied some of these conjuncts, says which
         ;; ones every row that came up has passed; those are skipped here
         ;; unless a row was kept on an error below.
         (below (prog1 (context-join-prefilter-report ctx) (setf (context-join-prefilter-report ctx) nil)))
         ;; Drops below that left this join no left rows: as written it may
         ;; have had some, and then it computes every right key (and raises
         ;; where one cannot be) before it finds that no row survives. Only
         ;; the rows as written can say, so the left side -- pure, or nothing
         ;; was handed down -- is evaluated again without them, and this join
         ;; runs as written.
         (val1 (if (and below (join-report-dropped below)
                        (or (value-null-p given1) (null (first-collection-item given1))))
                   (prog1 (args-eval a (args-node a 0))
                     (setf (context-join-prefilter-report ctx) nil
                           below nil))
                   given1)))
    (values val1 val2 below
            (and below (not (join-report-errored below)) (join-report-applied below)))))

(defun link-hash-buckets (a ctx val2 b1 b2 right-expr is-numeric facts)
  "The hash join's build phase over the right rows, with a reusable frame:
(values buckets right-facts). The rows are read in order here, where they are
close together, rather than scattered pair by pair in the projector. Sets
(CAR FACTS) to whether every row is flat."
  (let ((ht (make-hash-table :test #'equal))
        (right-facts (make-join-right-facts)))
    (with-row-binder (frame2 set-right) b2 ("_2")
      (let ((flat-row-p (make-join-flat-test
                         (append (join-binder-keys b1 "_1") (join-binder-keys b2 "_2"))))
            (flat t))
        (ctx-push-frame ctx frame2)
        (unwind-protect
             (for-each-collection-item (item2 val2)
               (let ((r2 (ensure-row-table-alias item2 b2)))
                 (when (and flat (not (funcall flat-row-p r2)))
                   (setf flat nil))
                 (set-right r2)
                 (let* ((key-val (args-eval a right-expr))
                        (key (extract-join-key key-val is-numeric)))
                   (note-right-join-key right-facts key)
                   (when (and key (not (join-key-bad-p key)))
                     (push r2 (gethash key ht))))))
          (ctx-pop-frame ctx))
        ;; Buckets were built by PUSH, newest first. Put them in row order
        ;; once, here, and not with a REVERSE per probing left row.
        (maphash (lambda (k v) (setf (gethash k ht) (nreverse v))) ht)
        (setf (car facts) flat)))
    (values ht right-facts)))

(defun link-prefilter-plan (val1 val2 is-left b1 b2 b1-names b2-names prefilter stages above above-keys
                            right-side obligations below applied-below)
  "The pre-filter, decided from the rows themselves (JOIN-STAGE-WALK):
(values prefix right-prefix left-before-right binders report). On a left row
a conjunct evaluates FALSE the row is dropped -- the joined rows it would have
produced would all have been dropped by the same conjunct; likewise a right
row, whose joined rows are then not built. On an error the row is KEPT: the
full predicate runs over the joined rows afterwards and raises there, in row
order."
  (let* ((prefix '())
         (right-prefix '())
         ;; How many left conjuncts come before the first right one: with a
         ;; right row kept on an error, a later left conjunct may not drop a
         ;; left row -- the joined row would have raised in the right conjunct
         ;; first.
         (left-before-right nil)
         (binders '())
         (report (make-join-report :applied (make-hash-table :test #'eq)
                                   :dropped (and below (join-report-dropped below))))
         ;; A read through this join's right binder is the right element in
         ;; every joined row -- the binder is bound last (spec §7.4) -- unless
         ;; the left binder has the same name, or a join above rebinds it.
         (right-names (lambda (stage)
                        (if (zerop (third stage))
                            (list (ascii-upcase b2) "_2")
                            (list (ascii-upcase b2)))))
         (right-here (unless (or is-left (ascii-equal b1 b2))
                       (lambda (fields stage)
                         (let ((names (funcall right-names stage))
                               (upper (funcall above-keys stage)))
                           (every (lambda (f) (and (member f names :test #'string=)
                                                   (not (gethash f upper))))
                                  fields))))))
    (when prefilter
      (unless right-side
        (setf right-side (make-join-side-of val2 (join-row-keys val2 b2-names) is-left b2-names)))
      (setf binders (mapcar #'first stages))
      (let* ((left-side (make-join-side-of val1 (join-row-keys val1 b1-names) nil b1-names))
             (self-names (list (ascii-upcase b1) "_1"))
             ;; A joined row carries a left element's field exactly as the
             ;; element does whenever no right element has the name (§7.4, pair
             ;; by pair) -- no row depends on another.
             (owned-here (lambda (fields stage)
                           (fields-owned-by-left-p fields stage above-keys right-side)))
             ;; A handed-down join key that could raise on a dropped row, and
             ;; nothing is dropped.
             (safe (or (null obligations)
                       (join-keys-safe-p obligations left-side right-side above))))
        (loop for (c stage right-p)
                in (and safe
                        (join-stage-walk stages owned-here
                                         (lambda (reqs stage)
                                           (join-totality-p reqs left-side right-side
                                                            (subseq above 0 (min (third stage) (length above)))))
                                         right-here))
              do (setf (gethash (jconj-node c) (join-report-applied report)) t)
                 (unless (and applied-below (gethash (jconj-node c) applied-below))
                   (if right-p
                       (progn
                         (unless left-before-right (setf left-before-right (length prefix)))
                         (push (join-read-self (jconj-node c) (funcall right-names stage) (jconj-binder c))
                               right-prefix))
                       (push (if (some (lambda (f) (and (member f self-names :test #'string=)
                                                        (not (gethash f (join-side-first left-side)))))
                                       (jconj-fields c))
                                 (join-read-self (jconj-node c) self-names (jconj-binder c))
                                 (jconj-node c))
                             prefix))))
        (setf prefix (nreverse prefix)
              right-prefix (nreverse right-prefix))))
    (values prefix right-prefix left-before-right binders report)))

(defun link-hash-join (a ctx val1 val2 is-left prefilter stages deep above above-keys b1-names b2-names
                       right-side obligations b1 b2 below applied-below projector facts
                       left-expr right-expr is-numeric swapped)
  "The hash join (only with right rows: the probe phase evaluates the left key
per left row, and with no pairs PRED must not run at all): build, plan the
pre-filter, then probe the left rows in order."
  (multiple-value-bind (ht right-facts) (link-hash-buckets a ctx val2 b1 b2 right-expr is-numeric facts)
    (multiple-value-bind (prefix right-prefix left-before-right binders report)
        (link-prefilter-plan val1 val2 is-left b1 b2 b1-names b2-names prefilter stages above above-keys
                             right-side obligations below applied-below)
      (let ((out '())
            (nout 0))          ; rows joined so far, for the MAX_COLLECTION cap
        ;; A FILTER keeps its input's keys, so the rows dropped here still
        ;; count towards the numbering of the rows kept, unless nothing
        ;; observes it (DEEP). When the join key is a literal field of the row,
        ;; a row that HAS it may be rejected before its key is computed.
        (with-row-binder (frame1 set-left) b1 ("_1" "_")
          (let* ((rejected (and right-prefix (make-hash-table :test #'eq)))
                 (numbered (and (or prefix rejected) (not deep)))
                 (dropped nil)
                 (position 1)
                 (keyed '())
                 (fast-field (and prefix deep (eq (node-kind left-expr) :index)
                                  (node-l left-expr) (eq (node-kind (node-l left-expr)) :var)
                                  (node-r left-expr) (eq (node-kind (node-r left-expr)) :text)
                                  (member (ascii-upcase (node-s (node-l left-expr)))
                                          (list (ascii-upcase b1) "_1" "_") :test #'string=)
                                  (node-s (node-r left-expr))))
                 (binder-cells (loop for binder in (remove-duplicates binders :test #'string=)
                                     collect (or (find binder frame1 :key #'car :test #'string=)
                                                 (let ((cell (cons binder nil)))
                                                   (setf frame1 (append frame1 (list cell)))
                                                   cell)))))
            ;; The binders the conjuncts read come first: a frame is searched
            ;; in order, once per read, every row.
            (setf frame1 (append binder-cells (set-difference frame1 binder-cells :test #'eq)))
            (labels ((verdict (conjuncts row cells)
                       ;; 0: keep the row; 1: drop it; 2: keep it, a conjunct
                       ;; raised on it.
                       (dolist (cell cells) (setf (cdr cell) row))
                       (dolist (conjunct conjuncts 0)
                         (let ((keep (handler-case (as-bool (args-eval a conjunct) (node-pos conjunct))
                                       (sel-error ()
                                         (setf (join-report-errored report) t)
                                         (return 2)))))
                           (unless keep (return 1)))))
                     (emit (joined)
                       (check-collection-cap (incf nout) (args-pos a))
                       (if numbered
                           (push (cons (format-index-string position) joined) keyed)
                           (push joined out))
                       (incf position)))
              ;; The right rows the right conjuncts reject, once each, after
              ;; every right key was computed. They stay in their buckets: a
              ;; left row still counts them towards the numbering, and one kept
              ;; on an error joins them.
              (when rejected
                (let* ((cells (loop for binder in (remove-duplicates binders :test #'string=)
                                    collect (cons binder nil)))
                       (before (join-report-errored report)))
                  (setf (join-report-errored report) nil)
                  (ctx-push-frame ctx cells)
                  (unwind-protect
                       (maphash (lambda (key bucket)
                                  (declare (ignore key))
                                  (dolist (r2 bucket)
                                    (when (= (verdict right-prefix r2 cells) 1)
                                      (setf (gethash r2 rejected) t))))
                                ht)
                    (ctx-pop-frame ctx))
                  (when (join-report-errored report)
                    (setf prefix (subseq prefix 0 left-before-right)
                          fast-field (and prefix fast-field)))
                  (setf (join-report-errored report) (or (join-report-errored report) before))))
              ;; A prefix that reads the left element only through fields other
              ;; than its relation's name is asked of the element as it arrives,
              ;; before it is extended: a row it drops is never extended.
              (let ((raw (and prefix
                              (every (lambda (c) (join-raw-safe-p c binders (ascii-upcase b1))) prefix)
                              (or (null fast-field) (not (ascii-equal fast-field b1))))))
                (ctx-push-frame ctx frame1)
                (unwind-protect
                     (for-each-collection-item (item1 val1)
                       (block row
                         (let ((r1 nil)
                               (asked nil))
                           (when raw
                             (setf asked (verdict prefix item1 binder-cells))
                             ;; A dropped row whose key is a field it has cannot
                             ;; raise in the key.
                             (when (and (= asked 1) fast-field (value-get item1 fast-field))
                               ;; Dropped before its key was computed -- but the
                               ;; key is this very field, and a rejected one
                               ;; still raises in the join as written.
                               (check-join-pair (extract-join-key (value-get item1 fast-field) is-numeric)
                                                right-facts left-expr right-expr is-numeric swapped)
                               (setf dropped t)
                               (return-from row)))
                           (setf r1 (ensure-row-table-alias item1 b1))
                           (when (and (not asked) fast-field (value-get r1 fast-field))
                             (setf asked (verdict prefix r1 binder-cells))
                             (when (= asked 1)
                               (check-join-pair (extract-join-key (value-get r1 fast-field) is-numeric)
                                                right-facts left-expr right-expr is-numeric swapped)
                               (setf dropped t)
                               (return-from row)))
                           (set-left r1)
                           (let* ((key-val (args-eval a left-expr))
                                  (key (extract-join-key key-val is-numeric))
                                  (matches (progn
                                             (check-join-pair key right-facts left-expr right-expr is-numeric swapped)
                                             (and key (not (join-key-bad-p key)) (gethash key ht)))))
                             (unless asked
                               (setf asked (if prefix (verdict prefix r1 binder-cells) 0)))
                             (when (= asked 1)
                               (setf dropped t)
                               (when numbered
                                 (incf position (cond (matches (length matches)) (is-left 1) (t 0))))
                               (return-from row))
                             (if matches
                                 ;; A left row kept on an error meets every right
                                 ;; row: its joined rows raise in the FILTER, in
                                 ;; order, where they would have.
                                 (let ((skip (and rejected (= asked 0) rejected)))
                                   (dolist (r2 matches)
                                     (if (and skip (gethash r2 skip))
                                         (progn (setf dropped t) (incf position))
                                         (emit (funcall projector r1 r2)))))
                                 (when is-left
                                   (emit (funcall projector r1 nil))))))))
                  (ctx-pop-frame ctx))))
            (when dropped (setf (join-report-dropped report) t))
            (when prefilter (setf (context-join-prefilter-report ctx) report))
            (when numbered
              (if (and dropped keyed)
                  (return-from link-hash-join
                    (%value-with-children :none nil (nreverse keyed) t))
                  (setf out (append (mapcar #'cdr keyed) out))))))
        (make-list-value (nreverse out))))))

(defun link-nested-loop (a ctx val1 val2 is-left prefilter b1 b2 pred-node sample-r2 projector)
  "The nested-loop join, for a predicate with no equi-join keys (or no right
rows): the joined rows in order, as a list value."
  (let ((out '())
        (nout 0))              ; rows joined so far, for the MAX_COLLECTION cap
    (with-row-binder (frame1 set-left) b1 ("_1" "_")
      (with-row-binder (frame2 set-right) b2 ("_2")
        (let ((frame (append frame1 frame2)))
          (ctx-push-frame ctx frame)
          (unwind-protect
               ;; Each side is listed ONCE (spec 7.3): the right side is walked
               ;; again for every left row, and a predicate that grows it must
               ;; not give later left rows more rows.
               (let* ((items1 (collection-items val1))
                      (items2 (collection-items val2))
                      (candidates nil)
                      (left-keys nil))
                 ;; `key == key AND residual`: only the pairs whose keys are
                 ;; equal can satisfy the predicate (see AND-RESIDUAL-CANDIDATES),
                 ;; so only those are run.
                 (when (and sample-r2 (null prefilter))
                   (ctx-pop-frame ctx)
                   (unwind-protect
                        (multiple-value-setq (left-keys candidates)
                          (and-residual-candidates a ctx pred-node b1 b2 items1 items2))
                     (ctx-push-frame ctx frame)))
                 (loop for item1 across items1
                       for i of-type fixnum from 0
                       do (let ((r1 (ensure-row-table-alias item1 b1))
                                (matched nil))
                            (set-left r1)
                            (when sample-r2
                              (flet ((try (item2)
                                       (let ((r2 (ensure-row-table-alias item2 b2)))
                                         (set-right r2)
                                         (when (as-bool (args-eval a pred-node) (node-pos pred-node))
                                           (setf matched t)
                                           (check-collection-cap (incf nout) (args-pos a))
                                           (push (funcall projector r1 r2) out)))))
                                (if candidates
                                    (dolist (j (gethash (svref left-keys i) candidates))
                                      (try (svref items2 j)))
                                    (loop for item2 across items2 do (try item2)))))
                            (when (and is-left (not matched))
                              (check-collection-cap (incf nout) (args-pos a))
                              (push (funcall projector r1 nil) out)))))
            (ctx-pop-frame ctx)))))
    (make-list-value (nreverse out))))

(defun do-link-rows (a ctx is-left prefilter stages deep above above-keys b1-names b2-names
                     right-side obligations b1 b2 pred-node)
  (multiple-value-bind (val1 val2 below applied-below) (link-sources a ctx)
    ;; Spec §7.4 "How a LINK evaluates": with no right elements PRED is never
    ;; evaluated. A LINK over an empty side is the empty list; a LINK_LEFT
    ;; with left rows but no right rows emits each unmatched row below
    ;; without touching PRED (the nested loop has no pairs to run it on).
    (let ((first-r1 (first-collection-item val1))
          (first-r2 (first-collection-item val2)))
      (if (or (value-null-p val1)
              (and (or (null first-r1) (null first-r2))
                   (or (not is-left) (null first-r1))))
          (make-list-value nil)
          ;; Every row is built from its own pair (spec §7.4): nothing is
          ;; decided from a first element except the shape of LINK_LEFT's null
          ;; record.
          (let* ((sample-r2 (when first-r2 (ensure-row-table-alias first-r2 b2)))
                 (null-r2 (when is-left
                            (let ((rec (make-null-record sample-r2 b2)))
                              (unless (value-null-p rec) rec))))
                 (facts (list nil))
                 (projector (make-join-projector b1 b2 null-r2 facts)))
            (multiple-value-bind (left-expr right-expr is-numeric swapped)
                (try-extract-equi-keys pred-node b1 b2)
              (if (and left-expr right-expr sample-r2)
                  (link-hash-join a ctx val1 val2 is-left prefilter stages deep above above-keys
                                  b1-names b2-names right-side obligations b1 b2 below applied-below
                                  projector facts left-expr right-expr is-numeric swapped)
                  (link-nested-loop a ctx val1 val2 is-left prefilter b1 b2 pred-node sample-r2 projector))))))))

;; Three or five arguments, refused at compile time like every E_ARITY (spec
;; §7.4); the rule is spec/builtins.json's and DEFINE-BUILTIN installs it.
(define-builtin "LINK" 3 5
  (lambda (a ctx) (do-link a ctx nil))
  :lazy t :binds t)

(define-builtin "LINK_LEFT" 3 5
  (lambda (a ctx) (do-link a ctx t))
  :lazy t :binds t)

