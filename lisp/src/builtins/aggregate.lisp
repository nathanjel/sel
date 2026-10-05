;;;; Aggregates. These are why SEL needs no loop: each evaluates one argument
;;;; node once per element, which is the same move IF makes, repeated.

(in-package #:sel)

;;; Whether NODE reads the variable VAR-NAME anywhere (used to skip building _K
;;; when a body never reads it).
(defun node-contains-var-p (node var-name)
  (when (and node (node-p node))
    (case (node-kind node)
      (:var (string= (node-s node) var-name))
      ((:bin :index) (or (node-contains-var-p (node-l node) var-name)
                         (node-contains-var-p (node-r node) var-name)))
      (:un (node-contains-var-p (node-l node) var-name))
      (:call (some (lambda (item) (node-contains-var-p item var-name)) (node-items node)))
      (:seq (some (lambda (item) (node-contains-var-p item var-name)) (node-items node)))
      (:list (some (lambda (item) (node-contains-var-p item var-name)) (node-items node)))
      (:assign (or (node-contains-var-p (node-l node) var-name)
                   (node-contains-var-p (node-r node) var-name)))
      (t nil))))

;;; Whether evaluating NODE might write into a value: it holds an assignment or
;;; calls a function the library does not ship (SHIPPED-CALL-P). A collector copies an element when it collects it
;;; (SPEC 3.4); while nothing below the body can write, deferring the copy to the
;;; end is unobservable, so only a body that might write copies at collection.
;;; Iterative: a body can be a flat chain as long as the source.
(defun node-may-write-p (node)
  (let ((stack (list node)))
    (loop while stack
          do (let ((n (pop stack)))
               (when (and n (node-p n))
                 (case (node-kind n)
                   (:assign (return-from node-may-write-p t))
                   (:call
                    (unless (shipped-call-p n)
                      (return-from node-may-write-p t))
                    (dolist (it (node-items n)) (push it stack)))
                   ((:seq :list) (dolist (it (node-items n)) (push it stack)))
                   ((:bin :index) (push (node-l n) stack) (push (node-r n) stack))
                   (:un (push (node-l n) stack))
                   (t nil)))))
    nil))

(defun snapshot-source (v)
  "The elements V has now, as a container of its own (SPEC 7.3): an aggregate
visits what its source held when it started, so a key the body adds is not
visited and a child it overwrites is still visited as it was. The ELEMENTS are
the source's own values -- the binder names the element itself (3.4), so a
mutation inside one is seen -- only the container is copied."
  (let ((c (copy-structure v)))
    (when (value-storage v)
      (setf (value-storage c) (copy-seq (the simple-vector (value-storage v)))))
    (when (value-children-internal v)
      (let ((pairs (mapcar (lambda (e) (cons (car e) (cdr e))) (value-children-internal v))))
        (setf (value-children-internal c) pairs
              (value-tail c) (last pairs))))
    (setf (value-index c) nil)
    c))

(defvar *index-text-cache*
  (let ((vec (make-array (1+ +index-cache-size+) :initial-element nil)))
    (loop for i from 1 to +index-cache-size+
          do (setf (aref vec i) (%text (svref *index-string-cache* i))))
    vec))

(declaim (inline format-index-text))
(defun format-index-text (n)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum n))
  (if (and (<= 1 n) (<= n +index-cache-size+))
      (svref (the simple-vector *index-text-cache*) n)
      (%text (format nil "~d" n))))

;;; The three representations a collection has -- a stored list, a shaped record,
;;; an alist -- walked by one macro rather than by a three-branch COND in every
;;; aggregate. Each branch is its own loop, so each runs as fast as the
;;; hand-written loop it replaced.
(defmacro do-elements ((item index &optional (key-text (gensym "KEY-TEXT"))
                                             (key-string (gensym "KEY-STRING")))
                       value &body body)
  "Run BODY once per element of VALUE, in order, with ITEM bound to the element
and INDEX to its 0-based position. KEY-TEXT and KEY-STRING name local functions
that return the element's key, as a SEL text value and as a string; a key is
made only when one is called. BODY may RETURN from the walk, whose value is
then the value of the form."
  (let ((v (gensym "V")) (storage (gensym "STORAGE")) (k (gensym "K")))
    (flet ((with-keys (text string)
             `(flet ((,key-text () ,text)
                     (,key-string () ,string))
                (declare (inline ,key-text ,key-string)
                         (ignorable (function ,key-text) (function ,key-string)))
                ,@body)))
      `(let ((,v ,value))
         (cond
           ((and (value-is-list ,v) (value-storage ,v))
            (let ((,storage (value-storage ,v)))
              (declare (type simple-vector ,storage))
              (loop for ,index of-type fixnum from 0 below (length ,storage)
                    for ,item = (svref ,storage ,index)
                    do ,(with-keys `(format-index-text (1+ ,index))
                                   `(format-index-string (1+ ,index))))))
           ((value-shape ,v)
            (let ((,storage (value-storage ,v)))
              (declare (type simple-vector ,storage))
              (loop for ,k in (record-shape-keys (value-shape ,v))
                    for ,index of-type fixnum from 0
                    for ,item = (svref ,storage ,index)
                    do ,(with-keys `(%text ,k) k))))
           (t
            (loop for (,k . ,item) in (aggregate-elements ,v)
                  for ,index of-type fixnum from 0
                  do ,(with-keys `(%text ,k) k))))))))

(defmacro with-binder-frame ((binder-cell k-cell) ctx binder needs-k &body body)
  "Run BODY with a frame pushed on CTX that binds BINDER -- its cell is
BINDER-CELL -- and, when NEEDS-K, `_K` (K-CELL, else NIL). The frame is popped
however BODY exits."
  (let ((c (gensym "CTX")))
    `(let* ((,c ,ctx)
            (,binder-cell (cons ,binder nil))
            (,k-cell (when ,needs-k (cons "_K" nil))))
       (ctx-push-frame ,c (if ,k-cell (list ,binder-cell ,k-cell) (list ,binder-cell)))
       (unwind-protect (progn ,@body)
         (ctx-pop-frame ,c)))))

(defun aggregate-walk (a ctx visit &optional (pass-key nil) (body-override nil))
  "Runs VISIT per element with the binder and _K in scope; BODY-OVERRIDE, when
given, is evaluated in place of the written body. A non-NIL return from VISIT
stops the walk and becomes the result."
  (let* ((three (= (args-count a) 3))
         (binder (if three (args-symbol a 1) "_"))
         (body (or body-override (args-node a (if three 2 1))))
         (val (snapshot-source (args-val a 0))))
    (unless (or (value-null-p val)
                (and (eq (value-kind val) :none) (zerop (value-size val))))
      (with-binder-frame (binder-cell k-cell) ctx binder (node-contains-var-p body "_K")
        (do-elements (item i key-text key-string) val
          (setf (cdr binder-cell) item)
          (when k-cell (setf (cdr k-cell) (key-text)))
          (let ((res (funcall visit (args-eval a body) (when pass-key (key-string)) item body)))
            (when res (return res))))))))

(define-builtin "ALL" 2 3
  (lambda (a ctx)
    (or (aggregate-walk a ctx
                        (lambda (r key item body)
                          (declare (ignore key item))
                          (unless (as-bool r (node-pos body)) (make-bool nil))))
        (make-bool t)))
  :lazy t :binds t)

(define-builtin "ANY" 2 3
  (lambda (a ctx)
    (or (aggregate-walk a ctx
                        (lambda (r key item body)
                          (declare (ignore key item))
                          (when (as-bool r (node-pos body)) (make-bool t))))
        (make-bool nil)))
  :lazy t :binds t)

(define-builtin "MAP" 2 3
  (lambda (a ctx)
    (let ((out '())
          ;; Once per call, not per element: the test scans a list of names.
          (fresh (node-fresh-p (args-node a (1- (args-count a))))))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (declare (ignore key item))
                        ;; Copied as it is collected (§3.4): the result shares nothing
                        ;; with the source, or with what the body returned -- unless
                        ;; the body builds it fresh, and then it is adopted.
                        (push (if fresh r (value-copy r (node-pos body))) out)
                        nil))
      (make-list-value (nreverse out))))
  :lazy t :binds t)

;;; FILTER over a join offers its conjuncts to the LINK, which tests what it
;;; can on the rows it joins: this FILTER's first -- it runs before the FILTER
;;; that handed the rest down -- then the handed ones. Deep drops, below the
;;; join directly under this FILTER, change its keys, so they are allowed only
;;; where nothing observes them (KEYS-UNOBSERVED, stamped by the physical
;;; optimiser). A stage is (binder jconjs above): ABOVE counts the joins
;;; between its FILTER and the join testing it. The two helpers run once per
;;; FILTER call, and only over a join.

(defun filter-offer-to-join (a ctx written handed)
  "This FILTER's leading field conjuncts, put in CTX's prefilter together with
the HANDED stages for the join below; returns them."
  (let* ((binder (if (= (args-count a) 3) (args-symbol a 1) "_"))
         (own (leading-field-conjuncts written binder))
         ;; A first conjunct that is neither a field test nor total ends every
         ;; walk before it starts: hand nothing, gather nothing.
         (blocked (let ((c (first own)))
                    (and c (not (jconj-field-only c)) (not (jconj-has-total c)))))
         (stages (unless blocked
                   (cons (list binder own 0)
                         (and handed (join-prefilter-stages handed))))))
    (when stages
      (setf (context-join-prefilter ctx)
            (make-join-prefilter :stages stages
                                 :deep (if handed t (and (node-keys-unobserved written) t))
                                 :above (and handed (join-prefilter-above handed))
                                 :obligations (and handed (join-prefilter-obligations handed)))))
    own))

(defun filter-unapplied-body (own report)
  "The conjuncts of OWN the join below did not apply (REPORT), joined by AND in
the source's order; NIL when it applied them all."
  (let ((rest (remove-if (lambda (c) (gethash (jconj-node c) (join-report-applied report))) own)))
    (when rest
      (let ((body (jconj-node (first rest))))
        (dolist (c (rest rest) body)
          (let ((and-node (make-node :bin (node-pos body))))
            (setf (node-s and-node) "AND"
                  (node-l and-node) body
                  (node-r and-node) (jconj-node c)
                  body and-node)))))))

;;; The one aggregate that preserves keys — a filtered list should still be
;;; addressable the way the original was.
(define-builtin "FILTER" 2 3
  (lambda (a ctx)
   (block filter-body
    (let* ((pairs '())
           (written (args-node a (1- (args-count a))))
           (handed (prog1 (context-join-prefilter ctx) (setf (context-join-prefilter ctx) nil)))
           (src (args-node a 0))
           (src-fresh (node-fresh-p src))
           (over-join (and src (eq (node-kind src) :call)
                           (member (node-s src) '("LINK" "LINK_LEFT") :test #'string=)
                           t))
           (own (when over-join (filter-offer-to-join a ctx written handed))))
      (unwind-protect (args-val a 0)
        (setf (context-join-prefilter ctx) nil))
      ;; The join's report -- which conjuncts every row that came up has
      ;; passed, whether a row was kept on an error, and whether any row was
      ;; dropped -- goes up as it is.
      (let* ((report (prog1 (context-join-prefilter-report ctx)
                       (setf (context-join-prefilter-report ctx) nil)))
             ;; The conjuncts of this FILTER the join below applied held on
             ;; every row it built, unless it kept a row on an error: then
             ;; they are TRUE there, raise nowhere, and only the rest is
             ;; evaluated, in the source's order (with none left, the join's
             ;; list is the FILTER's result as it is).
             (body-override
               (when (and over-join report (not (join-report-errored report))
                          (some (lambda (c) (gethash (jconj-node c) (join-report-applied report))) own))
                 (or (filter-unapplied-body own report)
                     (progn
                       (when handed (setf (context-join-prefilter-report ctx) report))
                       (return-from filter-body (args-val a 0)))))))
        (when (and report handed)
          (setf (context-join-prefilter-report ctx) report))
        (aggregate-walk a ctx
                        (lambda (r key item body)
                          (when (as-bool r (node-pos body))
                            (push (cons key (if src-fresh item (value-copy item (node-pos body)))) pairs))
                          nil)
                        t body-override)
        (if (null pairs)
            (%make-value :none nil nil t)
            (let ((entries (nreverse pairs)))
              (%value-with-children :none nil entries t)))))))
  :lazy t :binds t)

(define-builtin "SUM" 2 3
  (lambda (a ctx)
    (let ((total *dec-zero*))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (declare (ignore key item))
                        (setf total (dec-add total (as-dec r (node-pos body)) (node-pos body)))
                        nil))
      (make-num total)))
  :lazy t :binds t)

(define-builtin "JOIN" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((sep (args-text a 1))
           (at (args-pos-of a 0))
           (val (args-val a 0))
           (parts (mapcar (lambda (e) (as-text e at))
                          (cond ((and (value-is-list val) (value-storage val))
                                 (coerce (value-storage val) 'list))
                                (t (mapcar #'cdr (aggregate-elements val))))))
           ;; The joined length, worked out from the parts and refused past the
           ;; cap before the text is built (SPEC 6.4).
           (total (if parts
                      (+ (reduce #'+ parts :key #'length)
                         (* (length sep) (1- (length parts))))
                      0)))
      (check-text-cap total (args-pos a))
      (%text (with-output-to-string (out)
               (loop for part in parts
                     for first = t then nil
                     do (unless first (write-string sep out))
                        (write-string part out)))))))

(defun sort-leaf (v)
  "The value scalar context reads (SPEC 3.2: the first child, recursively), so a
record sorts by its first field and ranks by that field's kind. NIL when the
chain ends in nothing (NULL)."
  (if (not (eq (value-kind v) :none))
      v
      (handler-case (scalar-source v nil)
        (sel-error () nil))))

(defun sort-rank (v)
  "The kind rank of SPEC 7.3's total order: NULL < BOOL < numeric-looking text
(numbers included) < all other TEXT < BIN."
  (cond ((or (null v) (value-null-p v)) 0)
        ((eq (value-kind v) :bool) 1)
        ((looks-numeric v) 2)
        ((eq (value-kind v) :text) 3)
        ((eq (value-kind v) :bin) 4)
        (t 5)))

(defun make-bounded-heap (capacity greater-p)
  (let ((arr (make-array capacity :initial-element nil))
        (size 0))
    (labels ((sift-up (idx)
               (loop while (> idx 0)
                     for parent = (floor (1- idx) 2)
                     do (if (funcall greater-p (aref arr idx) (aref arr parent))
                            (progn
                              (rotatef (aref arr idx) (aref arr parent))
                              (setf idx parent))
                            (return))))
             (sift-down (idx)
               (loop for left = (1+ (* 2 idx))
                     for right = (+ 2 (* 2 idx))
                     while (< left size)
                     for best = (if (and (< right size)
                                         (funcall greater-p (aref arr right) (aref arr left)))
                                    right
                                    left)
                     do (if (funcall greater-p (aref arr best) (aref arr idx))
                            (progn
                              (rotatef (aref arr best) (aref arr idx))
                              (setf idx best))
                            (return))))
             (push-item (item)
               (if (< size capacity)
                   (progn
                     (setf (aref arr size) item)
                     (incf size)
                     (sift-up (1- size)))
                   (when (funcall greater-p (aref arr 0) item)
                     (setf (aref arr 0) item)
                     (sift-down 0)))))
      (values #'push-item
              (lambda ()
                (coerce (subseq arr 0 size) 'list))))))

;;; A sort key decorated ONCE with what comparing needs: its rank in SPEC 7.3's
;;; order and, within a rank, the value to compare (BOOL as 0/1, a number as its
;;; DEC, other text and BIN as octets). COMPARE-VALUES re-derived all of that on
;;; every comparison -- re-encoding both texts to UTF-8 and re-parsing both as
;;; decimals, n log n times (50k text keys took 0.9 s and 200 MB).
;;; Nothing here can signal: the leaf is a BOOL/TEXT/BIN scalar or nothing.
(defstruct (sort-key (:constructor %make-sort-key (rank payload)))
  (rank 0 :type fixnum)
  payload)

(defun make-sort-key (key)
  (let* ((leaf (sort-leaf key))
         (rank (sort-rank leaf)))
    (%make-sort-key rank
                    (case rank
                      (1 (if (value-scalar leaf) 1 0))
                      (2 (as-dec leaf))
                      ((3 4) (as-bytes leaf))
                      (t nil)))))

(defun compare-sort-keys (a b)
  "COMPARE-VALUES on decorated keys: the same order, without the repeated work."
  (let ((ra (sort-key-rank a))
        (rb (sort-key-rank b)))
    (cond ((< ra rb) -1)
          ((> ra rb) 1)
          (t (case ra
               (1 (let ((av (sort-key-payload a)) (bv (sort-key-payload b)))
                    (declare (type fixnum av bv))
                    (cond ((< av bv) -1) ((> av bv) 1) (t 0))))
               (2 (dec-cmp (sort-key-payload a) (sort-key-payload b)))
               ((3 4) (bytes-compare (sort-key-payload a) (sort-key-payload b)))
               (t 0))))))

(defstruct (sort-item (:constructor %make-sort-item (item key idx)))
  item
  key                                   ; a SORT-KEY
  (idx 0 :type fixnum))

(defun make-sort-item (item key idx)
  (%make-sort-item item (make-sort-key key) idx))

;;; SORT and its variants collect the elements into a new list, and §3.4 has the
;;; aggregates copy what they collect: the copy is made as the element is
;;; collected, so the result never shares structure with the source.
(declaim (inline make-copied-sort-item))
(defun make-copied-sort-item (item key idx fresh)
  "FRESH: the source is a value nothing else holds, so its elements need no copy."
  (make-sort-item (if fresh item (value-copy item)) key idx))

(defun decode-sort-call (a forms forced-dir counted)
  "(values binder body direction) of a SORT-family call A under FORMS, its
builtin's manifest forms (CALL-FORM; COUNTED for the TOP family). The binder is
read as a bare name (E_EXPECT_SYMBOL otherwise), then a direction argument is
evaluated and checked (E_BAD_ARG at it unless it is ASC or DESC, any case):
SPEC 7.4 has both checked whatever the list holds. BODY is NIL for a keyless
sort, and FORCED-DIR is SORT's and SORT_DESC's own direction."
  (multiple-value-bind (b key k2 d) (call-form forms (args-nodes a) counted)
    (declare (ignore k2))
    (let ((binder (if b (args-symbol a b) "_"))
          (direction (cond (forced-dir)
                           (d (ascii-upcase (args-text a d)))
                           (t "ASC"))))
      (unless (or (string= direction "ASC") (string= direction "DESC"))
        (fail "E_BAD_ARG" "sort direction must be 'ASC' or 'DESC'" (args-pos-of a d)))
      (values binder (and key (args-node a key)) direction))))

(defun do-sort (a ctx forced-dir forms)
  (let* ((val (args-val a 0))
         (fresh (node-fresh-p (args-node a 0))))
    (multiple-value-bind (binder body direction) (decode-sort-call a forms forced-dir nil)
      ;; A scalar is one element (SPEC 7.3), so its sort key is evaluated -- only
      ;; NULL and an empty list have nothing to sort; the direction was checked
      ;; first whatever the list holds (SPEC 7.4).
      (when (or (value-null-p val) (and (zerop (value-size val)) (eq (value-kind val) :none)))
        (return-from do-sort (make-list-value nil)))
      (let ((val (snapshot-source val))
            (desc (string= direction "DESC"))
            (indexed '()))
        (if (null body)
            (do-elements (item i) val
              (push (make-copied-sort-item item item i fresh) indexed))
            (with-binder-frame (binder-cell k-cell) ctx binder (node-contains-var-p body "_K")
              (do-elements (item i key-text) val
                (setf (cdr binder-cell) item)
                (when k-cell (setf (cdr k-cell) (key-text)))
                (push (make-copied-sort-item item (args-eval a body) i fresh) indexed))))
        (setf indexed
              (stable-sort (nreverse indexed)
                           (lambda (x y)
                             (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                               (when desc (setf c (- c)))
                               (< c 0)))))
        (make-list-value
         (loop for x in indexed
               collect (sort-item-item x)))))))

(defun do-top-sort (a ctx forced-dir forms)
  ;; Spec §7.4: a count of zero still evaluates the list, so the list is
  ;; evaluated (and its errors reported) before N is read, as in every other
  ;; host. The optimiser fuses SORT .> TAKE(0) into this, so the order matters
  ;; there too.
  (let* ((val (args-val a 0))
         (fresh (node-fresh-p (args-node a 0)))
         (limit (args-non-neg-int a (1- (args-count a)))))
    ;; Direction and count are always evaluated and checked (SPEC 7.4), whatever
    ;; the list holds; only then may an empty list or a zero count end the call.
    (multiple-value-bind (binder body direction) (decode-sort-call a forms forced-dir t)
      (when (or (zerop limit) (value-null-p val)
                (and (zerop (value-size val)) (eq (value-kind val) :none)))
        (return-from do-top-sort (make-list-value nil)))
      (let* ((val (snapshot-source val))
             (desc (string= direction "DESC"))
             (greater-fn (if desc
                             (lambda (x y)
                               (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                 (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (< c 0))))
                             (lambda (x y)
                               (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                 (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (> c 0))))))
             ;; Collected once its key is computed (SPEC 3.4): a key that might
             ;; write copies the element then, so a later key's write cannot
             ;; reach it.
             (eager (and body (node-may-write-p body))))
        (multiple-value-bind (push-item get-items)
            (make-bounded-heap (min limit (max 1 (value-size val))) greater-fn)
          (if (null body)
              (do-elements (item i) val
                (funcall push-item (make-sort-item item item i)))
              (with-binder-frame (binder-cell k-cell) ctx binder (node-contains-var-p body "_K")
                (do-elements (item i key-text) val
                  (setf (cdr binder-cell) item)
                  (when k-cell (setf (cdr k-cell) (key-text)))
                  (let ((key (args-eval a body)))
                    (funcall push-item (make-sort-item (if eager (value-copy item) item) key i))))))
          (let ((items (funcall get-items)))
            (setf items (stable-sort items
                                     (lambda (x y)
                                       (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                         (if (zerop c)
                                             (< (sort-item-idx x) (sort-item-idx y))
                                             (progn
                                               (when desc (setf c (- c)))
                                               (< c 0)))))))
            ;; The survivors only are copied (§3.4): TOP collects n elements, and
            ;; copying the rest would be the cost of a full SORT.
            (make-list-value
             (loop for x in items
                   collect (if (or fresh eager)
                               (sort-item-item x)
                               (value-copy (sort-item-item x)))))))))))

(define-builtin "SORT" 1 3
  (lambda (a ctx) (do-sort a ctx "ASC" (load-time-value (binding-forms-named "SORT"))))
  :lazy t :binds t)

(define-builtin "SORT_DESC" 1 3
  (lambda (a ctx) (do-sort a ctx "DESC" (load-time-value (binding-forms-named "SORT_DESC"))))
  :lazy t :binds t)

(define-builtin "SORT_BY" 2 4
  (lambda (a ctx) (do-sort a ctx nil (load-time-value (binding-forms-named "SORT_BY"))))
  :lazy t :binds t)

(define-builtin "TOP" 2 4
  (lambda (a ctx) (do-top-sort a ctx "ASC" (load-time-value (binding-forms-named "TOP"))))
  :lazy t :binds t)

(define-builtin "TOP_DESC" 2 4
  (lambda (a ctx) (do-top-sort a ctx "DESC" (load-time-value (binding-forms-named "TOP_DESC"))))
  :lazy t :binds t)

(define-builtin "TOP_BY" 3 5
  (lambda (a ctx) (do-top-sort a ctx nil (load-time-value (binding-forms-named "TOP_BY"))))
  :lazy t :binds t)

(defstruct (group-entry (:constructor make-group-entry (key key-str)))
  (key nil)
  (key-str "" :type string)
  (rows '() :type list))

;; The hash must agree with VALUE-EQL, which compares text by spelling (a number
;; and the text spelt the same are one key, and a decimal cache warmed on one of
;; them by arithmetic changes nothing). So it hashes the spelling, never the
;; cache: hashing the decimal fields put `X` and `"5"` in different buckets once
;; `X * 1` had been evaluated -- VALUE-SCALAR-HASH does. A scalar key is hashed
;; by its scalar alone, but a list or record key is walked, and the walk must
;; meet the depth cap as every other one does (spec §6.4).
(defun bucket-key-hash (v)
  (if (zerop (value-size v)) (value-scalar-hash v) (value-hash v)))

(defun bucket-key-text (key pos)
  (let ((v key))
    (when (eq (value-kind v) :none)
      (when (value-null-p v) (fail "E_NULL" "value is NULL" pos))
      (fail "E_NOT_TEXT" "a bucket key must be text or a number, got a list or record" pos))
    (as-text v pos)))

(defun do-bucket (a ctx forms)
  (let* ((val (snapshot-source (args-val a 0)))
         (src-fresh (node-fresh-p (args-node a 0))))
    ;; A scalar is one element (SPEC 7.3); only NULL and an empty list have none.
    (when (or (value-null-p val)
              (and (zerop (value-size val)) (eq (value-kind val) :none)))
      (return-from do-bucket (make-list-value nil)))
    (multiple-value-bind (b key agg) (call-form forms (args-nodes a))
      (let* ((binder (if b (args-symbol a b) "_"))
             (key-node (args-node a key))
             (agg-node (and agg (args-node a agg)))
             (groups-table (make-hash-table :test #'eql))
             (groups '())
             ;; A row is collected when its key is computed and it is grouped
             ;; (SPEC 3.4): when the key or the projection might write, it is
             ;; copied then, so neither a later key nor the projection can
             ;; change a row already grouped.
             (eager (or (node-may-write-p key-node) (and agg-node (node-may-write-p agg-node)))))
        (with-binder-frame (binder-cell k-cell) ctx binder (node-contains-var-p key-node "_K")
          (do-elements (source i key-text) val
            (setf (cdr binder-cell) source)
            (when k-cell (setf (cdr k-cell) (key-text)))
            (let* ((eval-key (args-eval a key-node))
                   (item (if eager (value-copy source) source))
                   ;; A bare bucket's key is an index key (spec §3.3): the
                   ;; scalar, verbatim, and refused the way indexing refuses it
                   ;; -- never collapsed onto a string that stands for every
                   ;; list, record or NULL. The projected spelling has no map to
                   ;; key and groups by identity instead.
                   (key-str (if (null agg-node)
                                (bucket-key-text eval-key (node-pos key-node))
                                ""))
                   ;; The bare spelling groups by the index key it has just been
                   ;; given (spec 3.3, 7.3): two keys with the same text are one
                   ;; group whatever their structure. The projected spelling
                   ;; groups by identity.
                   (h (if (null agg-node) (sxhash key-str) (bucket-key-hash eval-key)))
                   (bucket (gethash h groups-table))
                   (found (find-if (lambda (g)
                                     (if (null agg-node)
                                         (string= (group-entry-key-str g) key-str)
                                         (value-eql (group-entry-key g) eval-key)))
                                   bucket)))
              (if found
                  (push item (group-entry-rows found))
                  (let ((new-g (make-group-entry eval-key key-str)))
                    (setf (group-entry-rows new-g) (list item))
                    (setf (gethash h groups-table) (cons new-g bucket))
                    (push new-g groups))))))
        (setf groups (nreverse groups))
        (dolist (g groups)
          (setf (group-entry-rows g) (nreverse (group-entry-rows g))))
        (if (null agg-node)
            (let ((out (make-none)))
              (dolist (g groups)
                (value-set out (group-entry-key-str g)
                           (make-list-value (if (or src-fresh eager)
                                                (group-entry-rows g)
                                                (mapcar #'value-copy (group-entry-rows g))))))
              out)
            (let ((out '()))
              (with-binder-frame (agg-binder-cell agg-k-cell) ctx binder t
                (dolist (g groups)
                  (setf (cdr agg-binder-cell) (make-list-value (group-entry-rows g))
                        (cdr agg-k-cell) (group-entry-key g))
                  (let ((res (args-eval a agg-node)))
                    (push (if (node-fresh-p agg-node) res (value-copy res (node-pos agg-node))) out))))
              (make-list-value (nreverse out))))))))

(define-builtin "BUCKET" 2 4
  (lambda (a ctx) (do-bucket a ctx (load-time-value (binding-forms-named "BUCKET"))))
  :lazy t :binds t)


