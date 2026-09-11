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
          (let ((keys (loop for i from 0 below n by 2 collect (args-text a i))))
            (if (= (length (remove-duplicates keys :test #'string=)) num-fields)
                (let* ((shape (get-record-shape keys))
                       (storage (make-array num-fields)))
                  (loop for i from 0 below n by 2
                        for slot-idx from 0
                        do (setf (svref storage slot-idx) (value-copy (args-val a (1+ i)))))
                  (%make-shaped-value shape storage))
                (let ((rec (make-none)))
                  (loop for i from 0 below n by 2
                        do (value-set rec (args-text a i) (value-copy (args-val a (1+ i)))))
                  rec))))))
  :arity-error (lambda (count)
                 (when (oddp count)
                   (format nil "RECORD takes an even number of arguments (key-value pairs), got ~d" count))))

(define-builtin "LAZY_RECORD" 0 +variadic+
  (lambda (a ctx)
    (let ((n (args-count a)))
      (when (oddp n)
        (fail "E_BAD_ARG" (format nil "LAZY_RECORD takes an even number of arguments (key-value pairs), got ~d" n)
              (args-pos-of a 0)))
      (let ((rec (make-none)))
        (loop for i from 0 below n by 2
              for key-node = (args-node a i)
              for val-node = (args-node a (1+ i))
              for key = (as-text (eval-node key-node ctx) (node-pos key-node))
              do (let ((val-val
                         (case (node-kind val-node)
                           ((:num :text :bool :null)
                            (eval-node val-node ctx))
                           (t
                            (let ((captured-ctx (ctx-copy-current ctx))
                                  (captured-node val-node))
                              (make-thunk-value
                               (lambda ()
                                 (eval-node captured-node captured-ctx))))))))
                   (value-set rec key val-val)))
        rec)))
  :lazy t :binds t
  :arity-error (lambda (count)
                 (when (oddp count)
                   (format nil "LAZY_RECORD takes an even number of arguments (key-value pairs), got ~d" count))))

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
       (force-value ,v-g)
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
  (force-value v)
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

(defun canonical-numeric-string (sc)
  (declare (type string sc))
  (let ((len (length sc)))
    (if (and (plusp len)
             (or (char/= (char sc 0) #\0) (= len 1))
             (loop for i from 0 below len
                   always (digit-char-p (char sc i))))
        sc
        (let ((d (dec-parse sc)))
          (when d (dec-format d))))))

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
  (let ((null-rec (make-none)))
    (when sample-row
      (dolist (k (value-keys sample-row))
        (value-set null-rec k (make-none))))
    (when tbl-name
      (let ((low (string-downcase tbl-name)))
        (value-set null-rec tbl-name (make-none))
        (when (string/= tbl-name low)
          (value-set null-rec low (make-none)))))
    null-rec))

(defun ensure-row-table-alias (row tbl-name)
  (if (or (null tbl-name) (string= tbl-name "_1") (value-has row tbl-name))
      row
      (let* ((low (string-downcase tbl-name))
             (diff (string/= tbl-name low)))
        (cond
          ((value-shape row)
           (let* ((old-shape (value-shape row))
                  (cached (gethash tbl-name (record-shape-alias-cache old-shape))))
             (multiple-value-bind (new-shape is-diff old-len)
                 (if cached
                     (values (first cached) (second cached) (third cached))
                     (let* ((old-keys (record-shape-keys old-shape))
                            (new-keys (if diff
                                          (append old-keys (list tbl-name low))
                                          (append old-keys (list tbl-name))))
                            (ns (get-record-shape new-keys))
                            (olen (record-shape-size old-shape)))
                       (setf (gethash tbl-name (record-shape-alias-cache old-shape))
                             (list ns diff olen))
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
           (let* ((extra (if diff
                             (list (cons tbl-name row) (cons low row))
                             (list (cons tbl-name row))))
                  (new-children (append (value-children row) extra)))
             (%value-with-children (value-kind row) (value-scalar row) new-children (value-is-list row))))))))

(defun make-joined-row (r1 r2 b1 b2 &optional pre-promoted-k1 pre-promoted-k2 pre-table-k1)
  (let ((out-children '())
        (b1-low (string-downcase b1))
        (b2-low (string-downcase b2)))
    ;; Preserve any existing table records from r1 (for chained links)
    (if pre-table-k1
        (dolist (k pre-table-k1)
          (let ((child (value-get r1 k)))
            (when child (push (cons k child) out-children))))
        (dolist (c (value-children r1))
          (let ((child (cdr c)))
            (when (and child (value-children child) (not (value-is-list child)))
              (push (cons (car c) child) out-children)))))
    ;; Set explicit binder subrecords
    (push (cons b1 r1) out-children)
    (when (string/= b1 b1-low)
      (push (cons b1-low r1) out-children))
    (when (string/= b1 "_1")
      (push (cons "_1" r1) out-children))
    (if r2
        (progn
          (push (cons b2 r2) out-children)
          (when (string/= b2 b2-low)
            (push (cons b2-low r2) out-children))
          (when (string/= b2 "_2")
            (push (cons "_2" r2) out-children)))
        (let ((null-val (make-none)))
          (push (cons b2 null-val) out-children)
          (when (string/= b2 b2-low)
            (push (cons b2-low null-val) out-children))
          (when (string/= b2 "_2")
            (push (cons "_2" null-val) out-children))))
    ;; Promote unambiguous scalar columns
    (if (and pre-promoted-k1 (or pre-promoted-k2 (null r2)))
        (progn
          (dolist (k pre-promoted-k1)
            (let ((v (value-get r1 k)))
              (when v (push (cons k v) out-children))))
          (when r2
            (dolist (k pre-promoted-k2)
              (let ((v (value-get r2 k)))
                (when (and v (not (value-null-p v)))
                  (push (cons k v) out-children))))))
        (let ((keys1 (value-keys r1))
              (keys2 (if r2 (value-keys r2) '())))
          (dolist (c (value-children r1))
            (let ((k (car c))
                  (v (cdr c)))
              (unless (or (and v (value-children v) (not (value-is-list v)))
                          (member k keys2 :test #'string-equal))
                (push (cons k v) out-children))))
          (when r2
            (dolist (c (value-children r2))
              (let ((k (car c))
                    (v (cdr c)))
                (unless (or (and v (value-children v) (not (value-is-list v)))
                            (value-null-p v)
                            (member k keys1 :test #'string-equal))
                  (push (cons k v) out-children)))))))
    (let* ((entries (nreverse out-children))
           (keys (mapcar #'car entries))
           (n (length keys)))
      (if (= (length (remove-duplicates keys :test #'string=)) n)
          (let* ((shape (get-record-shape keys))
                 (storage (make-array n)))
            (loop for (nil . val) in entries
                  for idx from 0
                  do (setf (svref storage idx) val))
            (%make-shaped-value shape storage))
          (%value-with-children :none nil entries nil)))))

(defun make-join-projector (sample-r1 sample-r2 b1 b2 promoted-k1 promoted-k2 table-k1 null-r2)
  (let* ((sample-joined (when (and sample-r1 sample-r2)
                          (make-joined-row sample-r1 sample-r2 b1 b2 promoted-k1 promoted-k2 table-k1)))
         (shape (when sample-joined (value-shape sample-joined))))
    (if (null shape)
        (lambda (r1 r2)
          (if r2
              (make-joined-row r1 r2 b1 b2 promoted-k1 promoted-k2 table-k1)
              (make-joined-row r1 null-r2 b1 b2 promoted-k1 promoted-k2 table-k1)))
        (let* ((keys (record-shape-keys shape))
               (n (length keys))
               (r1-shape (value-shape sample-r1))
               (r2-shape (when sample-r2 (value-shape sample-r2)))
               (b1-low (string-downcase b1))
               (b2-low (string-downcase b2))
               (actions (make-array n)))
          (loop for k in keys
                for idx from 0
                do (setf (aref actions idx)
                         (cond
                           ((or (string= k b1) (string= k b1-low) (string= k "_1"))
                            (list :r1))
                           ((or (string= k b2) (string= k b2-low) (string= k "_2"))
                            (list :r2))
                           ((or (member k table-k1 :test #'string=)
                                (member k promoted-k1 :test #'string=))
                            (if (and r1-shape (gethash k (record-shape-key-map r1-shape)))
                                (list :r1-slot (gethash k (record-shape-key-map r1-shape)))
                                (list :r1-get k)))
                           ((member k promoted-k2 :test #'string=)
                            (if (and r2-shape (gethash k (record-shape-key-map r2-shape)))
                                (list :r2-slot (gethash k (record-shape-key-map r2-shape)))
                                (list :r2-get k)))
                           (t (list :none)))))
          (lambda (r1 r2)
            (if (null r2)
                (make-joined-row r1 null-r2 b1 b2 promoted-k1 promoted-k2 table-k1)
                (let ((storage (make-array n)))
                  (loop for i from 0 below n
                        for act = (aref actions i)
                        for kind = (car act)
                        do (case kind
                             (:r1 (setf (svref storage i) r1))
                             (:r2 (setf (svref storage i) r2))
                             (:r1-slot (setf (svref storage i) (svref (value-storage r1) (second act))))
                             (:r1-get (setf (svref storage i) (or (value-get r1 (second act)) (make-none))))
                             (:r2-slot (setf (svref storage i) (svref (value-storage r2) (second act))))
                             (:r2-get (setf (svref storage i) (or (value-get r2 (second act)) (make-none))))
                             (:none (setf (svref storage i) (make-none)))))
                  (%make-shaped-value shape storage))))))))

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

(defun do-link (a ctx is-left)
  (let* ((count (args-count a))
         (val1 (args-val a 0))
         (val2 (args-val a 1))
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
    (if (value-null-p val1)
        (make-list-value nil)
        (let* ((first-r1 (first-collection-item val1))
               (first-r2 (first-collection-item val2))
               (sample-r1 (when first-r1 (ensure-row-table-alias first-r1 b1)))
               (sample-r2 (when first-r2 (ensure-row-table-alias first-r2 b2)))
               (null-r2 (when is-left (make-null-record sample-r2 b2)))
               (keys1 (if sample-r1 (value-keys sample-r1) '()))
               (keys2 (if sample-r2 (value-keys sample-r2) '()))
               (keys2-ht (make-hash-table :test #'equalp))
               (keys1-ht (make-hash-table :test #'equalp))
               (out '()))
          (dolist (k keys2) (setf (gethash k keys2-ht) t))
          (dolist (k keys1) (setf (gethash k keys1-ht) t))
          (let* ((promoted-k1 (loop for k in keys1
                                    for v = (value-get sample-r1 k)
                                    when (and (not (and v (value-children v) (not (value-is-list v))))
                                              (not (gethash k keys2-ht)))
                                    collect k))
                 (promoted-k2 (loop for k in keys2
                                    for v = (value-get sample-r2 k)
                                    when (and (not (and v (value-children v) (not (value-is-list v))))
                                              (not (gethash k keys1-ht)))
                                    collect k))
                 (table-k1 (loop for k in keys1
                                 for v = (value-get sample-r1 k)
                                 when (and v (value-children v) (not (value-is-list v)))
                                 collect k))
                 (projector (make-join-projector sample-r1 sample-r2 b1 b2 promoted-k1 promoted-k2 table-k1 null-r2)))
            (multiple-value-bind (left-expr right-expr is-numeric)
                (try-extract-equi-keys pred-node b1 b2)
              (if (and left-expr right-expr)
                  ;; --- HASH JOIN ---
                  (let ((ht (make-hash-table :test #'equal))
                        (b2-cell (cons b2 nil))
                        (b2-low-cell (cons (string-downcase b2) nil))
                        (b2-2-cell (cons "_2" nil)))
                    ;; Build phase on right relation with reusable frame
                    (let ((frame2 (list b2-cell b2-low-cell b2-2-cell)))
                      (ctx-push-frame ctx frame2)
                      (unwind-protect
                           (for-each-collection-item (item2 val2)
                             (let ((r2 (ensure-row-table-alias item2 b2)))
                               (setf (cdr b2-cell) r2
                                     (cdr b2-low-cell) r2
                                     (cdr b2-2-cell) r2)
                               (let* ((key-val (args-eval a right-expr))
                                      (key (extract-join-key key-val is-numeric)))
                                 (when key
                                   (push r2 (gethash key ht))))))
                        (ctx-pop-frame ctx)))
                    ;; Probe phase on left relation with reusable frame
                    (let ((b1-cell (cons b1 nil))
                          (b1-low-cell (cons (string-downcase b1) nil))
                          (b1-1-cell (cons "_1" nil))
                          (b1-_-cell (cons "_" nil)))
                      (let ((frame1 (list b1-cell b1-low-cell b1-1-cell b1-_-cell)))
                        (ctx-push-frame ctx frame1)
                        (unwind-protect
                             (for-each-collection-item (item1 val1)
                               (let ((r1 (ensure-row-table-alias item1 b1)))
                                 (setf (cdr b1-cell) r1
                                       (cdr b1-low-cell) r1
                                       (cdr b1-1-cell) r1
                                       (cdr b1-_-cell) r1)
                                 (let* ((key-val (args-eval a left-expr))
                                        (key (extract-join-key key-val is-numeric))
                                        (matches (and key (gethash key ht))))
                                   (if matches
                                       (dolist (r2 (reverse matches))
                                         (push (funcall projector r1 r2) out))
                                       (when is-left
                                         (push (funcall projector r1 nil) out))))))
                          (ctx-pop-frame ctx)))))
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
                               (for-each-collection-item (item2 val2)
                                 (let ((r2 (ensure-row-table-alias item2 b2)))
                                   (setf (cdr b2-cell) r2 (cdr b2-low-cell) r2 (cdr b2-2-cell) r2)
                                   (when (as-bool (args-eval a pred-node) (node-pos pred-node))
                                     (setf matched t)
                                     (push (funcall projector r1 r2) out))))
                               (when (and is-left (not matched))
                                 (push (funcall projector r1 nil) out))))
                        (ctx-pop-frame ctx))))))
            (make-list-value (nreverse out)))))))

(define-builtin "LINK" 3 5
  (lambda (a ctx) (do-link a ctx nil))
  :lazy t :binds t)

(define-builtin "LINK_LEFT" 3 5
  (lambda (a ctx) (do-link a ctx t))
  :lazy t :binds t)

