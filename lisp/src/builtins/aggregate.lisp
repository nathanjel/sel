;;;; Aggregates. These are why SEL needs no loop: each evaluates one argument
;;;; node once per element, which is the same move IF makes, repeated.

(in-package #:sel)

;;; Runs VISIT per element with the binder and _K in scope. A non-NIL return from
;;; VISIT stops the walk and becomes the result.
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
      (:group (node-contains-var-p (node-l node) var-name))
      (:assign (or (node-contains-var-p (node-l node) var-name)
                   (node-contains-var-p (node-r node) var-name)))
      (t nil))))

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
  (let ((vec (make-array 10001 :initial-element nil)))
    (loop for i from 1 to 10000
          do (setf (aref vec i) (%text (svref *index-string-cache* i))))
    vec))

(declaim (inline format-index-text))
(defun format-index-text (n)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum n))
  (if (and (<= 1 n) (<= n 10000))
      (svref (the simple-vector *index-text-cache*) n)
      (%text (format nil "~d" n))))

;;; Runs VISIT per element with the binder and _K in scope. A non-NIL return from
;;; VISIT stops the walk and becomes the result.
(defun aggregate-walk (a ctx visit &optional (pass-key nil) (body-override nil))
  "Runs VISIT per element with the binder and _K in scope; BODY-OVERRIDE, when
given, is evaluated in place of the written body."
  (let* ((three (= (args-count a) 3))
         (binder (if three (args-symbol a 1) "_"))
         (body (or body-override (args-node a (if three 2 1))))
         (val (snapshot-source (args-val a 0))))
    (unless (or (value-null-p val)
                (and (eq (value-kind val) :none) (zerop (value-size val))))
      (let* ((needs-k (node-contains-var-p body "_K"))
             (binder-cell (cons binder nil))
             (k-cell (when needs-k (cons "_K" nil)))
             (frame (if needs-k (list binder-cell k-cell) (list binder-cell))))
        (ctx-push-frame ctx frame)
        (flet ((eval-body () (args-eval a body)))
          (declare (inline eval-body))
        (unwind-protect
             (cond
               ;; Fast path: list value with storage vector
               ((and (value-is-list val) (value-storage val))
                (let ((storage (value-storage val)))
                  (loop for i from 0 below (length storage)
                        for item = (svref storage i)
                        do (setf (cdr binder-cell) item)
                           (let ((k-val (when needs-k (format-index-text (1+ i))))
                                 (k-str (when pass-key (format-index-string (1+ i)))))
                             (when needs-k
                               (setf (cdr k-cell) k-val))
                             (let ((res (funcall visit (eval-body) k-str item body)))
                               (when res (return res)))))))
               ;; Fast path: shaped record
               ((value-shape val)
                (let* ((shape (value-shape val))
                       (storage (value-storage val))
                       (keys (record-shape-keys shape)))
                  (loop for k in keys
                        for i from 0
                        for item = (svref storage i)
                        do (setf (cdr binder-cell) item)
                           (when needs-k
                             (setf (cdr k-cell) (%text k)))
                           (let ((res (funcall visit (eval-body) (when pass-key k) item body)))
                             (when res (return res))))))
               ;; General path
               (t
                (let ((elements (aggregate-elements val)))
                  (loop for (key . item) in elements
                        do (setf (cdr binder-cell) item)
                           (when needs-k
                             (setf (cdr k-cell) (%text key)))
                           (let ((res (funcall visit (eval-body) (when pass-key key) item body)))
                             (when res (return res)))))))
          (ctx-pop-frame ctx)))))))

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

;;; Bound by the sorts around their per-element copy: true when the source is fresh.
(defvar *collect-fresh* nil)

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

;;; The one aggregate that preserves keys — a filtered list should still be
;;; addressable the way the original was.
(define-builtin "FILTER" 2 3
  (lambda (a ctx)
   (block filter-body
    ;; Over a join, the conjuncts are offered to the LINK, which tests what
    ;; it can on the rows it joins (SEL-0052, SEL-0054): this FILTER's first
    ;; -- it runs before the FILTER that handed the rest down -- then the
    ;; handed ones. Deep drops, below the join directly under this FILTER,
    ;; change its keys, so they are allowed only where nothing observes them
    ;; (KEYS-UNOBSERVED, stamped by the physical optimiser). A stage is
    ;; (binder jconjs above): ABOVE counts the joins between its FILTER and
    ;; the join testing it.
    (let* ((pairs '())
           (written (args-node a (1- (args-count a))))
           (handed (prog1 (context-join-prefilter ctx) (setf (context-join-prefilter ctx) nil)))
           (src (args-node a 0))
           (src-fresh (node-fresh-p src))
           (own nil)
           (over-join nil))
      (when (and src (eq (node-kind src) :call) (member (node-s src) '("LINK" "LINK_LEFT") :test #'string=))
        (let ((binder (if (= (args-count a) 3) (args-symbol a 1) "_")))
          (setf own (leading-field-conjuncts written binder)
                over-join t)
          ;; A first conjunct that is neither a field test nor total ends
          ;; every walk before it starts: hand nothing, gather nothing.
          (let* ((blocked (let ((c (first own)))
                            (and c (not (jconj-field-only c)) (not (jconj-has-total c)))))
                 (stages (unless blocked
                           (cons (list binder own 0)
                                 (and handed (join-prefilter-stages handed))))))
            (when stages
              (setf (context-join-prefilter ctx)
                    (make-join-prefilter :stages stages
                                         :deep (if handed t (and (node-keys-unobserved written) t))
                                         :above (and handed (join-prefilter-above handed))
                                         :obligations (and handed (join-prefilter-obligations handed))))))))
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
                 (let ((rest (remove-if (lambda (c) (gethash (jconj-node c) (join-report-applied report))) own)))
                   (when (null rest)
                     (when handed (setf (context-join-prefilter-report ctx) report))
                     (return-from filter-body (args-val a 0)))
                   (let ((body (jconj-node (first rest))))
                     (dolist (c (rest rest) body)
                       (let ((and-node (make-node :bin (node-pos body))))
                         (setf (node-s and-node) "AND"
                               (node-l and-node) body
                               (node-r and-node) (jconj-node c)
                               body and-node))))))))
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

(defun compare-values (a b)
  "Three-way comparison in SPEC 7.3's total order: by kind rank, then within a
rank -- FALSE before TRUE, numbers by exact value, other text and BIN bytewise.
Every pair of values compares, and transitively, so a sort cannot depend on the
order it is handed its elements."
  (let* ((a (sort-leaf a))
         (b (sort-leaf b))
         (ra (sort-rank a))
         (rb (sort-rank b)))
    (cond
      ((< ra rb) -1)
      ((> ra rb) 1)
      (t
       (case ra
         (1 (let ((av (if (value-scalar a) 1 0))
                  (bv (if (value-scalar b) 1 0)))
              (cond ((< av bv) -1) ((> av bv) 1) (t 0))))
         (2 (dec-cmp (as-dec a) (as-dec b)))
         ((3 4) (bytes-compare (as-bytes a) (as-bytes b)))
         (t 0))))))

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
;;; decimals, n log n times (LISP-P4: 50k text keys took 0.9 s and 200 MB).
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
(defun make-copied-sort-item (item key idx)
  (make-sort-item (if *collect-fresh* item (value-copy item)) key idx))

(defun do-sort (a ctx forced-dir)
  (let* ((val (args-val a 0))
         (*collect-fresh* (node-fresh-p (args-node a 0))))
    ;; A scalar is one element (SPEC 7.3), so its sort key is evaluated -- only
    ;; NULL and an empty list have nothing to sort.
    ;; The direction is always evaluated and checked, even when there is nothing to
    ;; sort (SPEC 7.4): a bad one is an error whatever the list holds.
    (let ((empty (or (value-null-p val) (and (zerop (value-size val)) (eq (value-kind val) :none)))))
        (let* ((count (args-count a))
               direction
               binder
               body)
          (if (= count 1)
              (setf direction (or forced-dir "ASC"))
              (progn
                (cond
                  ((= count 2)
                   (setf binder "_"
                         body (args-node a 1)
                         direction (or forced-dir "ASC")))
                  ((= count 3)
                   (cond
                     (forced-dir
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction forced-dir))
                     ((eq (node-kind (args-node a 2)) :text)
                      (setf binder "_"
                            body (args-node a 1)
                            direction (ascii-upcase (args-text a 2))))
                     ((args-symbol-p a 1)
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction "ASC"))
                     (t
                      (setf binder "_"
                            body (args-node a 1)
                            direction (ascii-upcase (args-text a 2))))))
                  (t ; 4
                   (setf binder (args-symbol a 1)
                         body (args-node a 2)
                         direction (ascii-upcase (args-text a 3)))))
                (unless (or (string= direction "ASC") (string= direction "DESC"))
                  (let ((pos-idx (if (= count 4) 3 2)))
                    (fail "E_BAD_ARG" "sort direction must be 'ASC' or 'DESC'"
                          (args-pos-of a pos-idx))))))
          (when empty (return-from do-sort (make-list-value nil)))
          (let* ((val (snapshot-source val))
                 (desc (string= direction "DESC"))
                 (needs-k (and body (node-contains-var-p body "_K")))
                 (binder-cell (cons binder nil))
                 (k-cell (when needs-k (cons "_K" nil)))
                 (frame (if needs-k (list binder-cell k-cell) (list binder-cell)))
                 (indexed '()))
            (if (= count 1)
                (cond
                  ((and (value-is-list val) (value-storage val))
                   (let ((storage (value-storage val)))
                     (setf indexed
                           (loop for i from 0 below (length storage)
                                 for item = (svref storage i)
                                 collect (make-copied-sort-item item item i)))))
                  (t
                   (setf indexed
                         (loop for (nil . item) in (aggregate-elements val)
                               for idx from 0
                               collect (make-copied-sort-item item item idx)))))
                (progn
                  (ctx-push-frame ctx frame)
                  (unwind-protect
                       (cond
                         ((and (value-is-list val) (value-storage val))
                          (let ((storage (value-storage val)))
                            (setf indexed
                                  (loop for i from 0 below (length storage)
                                        for item = (svref storage i)
                                        do (setf (cdr binder-cell) item)
                                           (when needs-k
                                             (setf (cdr k-cell) (format-index-text (1+ i))))
                                        collect (make-copied-sort-item item (args-eval a body) i)))))
                         ((value-shape val)
                          (let* ((shape (value-shape val))
                                 (storage (value-storage val))
                                 (keys (record-shape-keys shape)))
                            (setf indexed
                                  (loop for k in keys
                                        for i from 0
                                        for item = (svref storage i)
                                        do (setf (cdr binder-cell) item)
                                           (when needs-k
                                             (setf (cdr k-cell) (%text k)))
                                        collect (make-copied-sort-item item (args-eval a body) i)))))
                         (t
                          (setf indexed
                                (loop for (k . item) in (aggregate-elements val)
                                      for idx from 0
                                      do (setf (cdr binder-cell) item)
                                         (when needs-k
                                           (setf (cdr k-cell) (%text k)))
                                      collect (make-copied-sort-item item (args-eval a body) idx)))))
                    (ctx-pop-frame ctx))))
            (setf indexed
                  (stable-sort indexed
                               (lambda (x y)
                                 (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                   (when desc (setf c (- c)))
                                   (< c 0)))))
            (make-list-value
             (loop for x in indexed
                   collect (sort-item-item x))))))))

(defun do-top-sort (a ctx forced-dir)
  ;; Spec §7.4: a count of zero still evaluates the list, so the list is
  ;; evaluated (and its errors reported) before N is read, as in every other
  ;; host. The optimiser fuses SORT .> TAKE(0) into this, so the order matters
  ;; there too.
  (let* ((count (args-count a))
         (val (args-val a 0))
         (fresh (node-fresh-p (args-node a 0)))
         (limit (args-non-neg-int a (1- count))))
    ;; Direction and count are always evaluated and checked (SPEC 7.4), whatever
    ;; the list holds; only then may an empty list or a zero count end the call.
    (let ((empty (or (zerop limit) (value-null-p val)
                     (and (zerop (value-size val)) (eq (value-kind val) :none)))))
        (let* ((sort-count (1- count))
               direction
               binder
               body)
          (if (= sort-count 1)
              (setf direction (or forced-dir "ASC"))
              (progn
                (cond
                  ((= sort-count 2)
                   (setf binder "_"
                         body (args-node a 1)
                         direction (or forced-dir "ASC")))
                  ((= sort-count 3)
                   (cond
                     (forced-dir
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction forced-dir))
                     ((eq (node-kind (args-node a 2)) :text)
                      (setf binder "_"
                            body (args-node a 1)
                            direction (ascii-upcase (args-text a 2))))
                     ((args-symbol-p a 1)
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction "ASC"))
                     (t
                      (setf binder "_"
                            body (args-node a 1)
                            direction (ascii-upcase (args-text a 2))))))
                  (t ; 4
                   (setf binder (args-symbol a 1)
                         body (args-node a 2)
                         direction (ascii-upcase (args-text a 3)))))
                (unless (or (string= direction "ASC") (string= direction "DESC"))
                  (let ((pos-idx (if (= sort-count 4) 3 2)))
                    (fail "E_BAD_ARG" "sort direction must be 'ASC' or 'DESC'"
                          (args-pos-of a pos-idx))))))
          (when empty (return-from do-top-sort (make-list-value nil)))
          (let* ((val (snapshot-source val))
                 (desc (string= direction "DESC"))
                 (greater-fn (if desc
                                 (lambda (x y)
                                   (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                     (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (< c 0))))
                                 (lambda (x y)
                                   (let ((c (compare-sort-keys (sort-item-key x) (sort-item-key y))))
                                     (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (> c 0))))))
                 (needs-k (and body (node-contains-var-p body "_K")))
                 (binder-cell (cons binder nil))
                 (k-cell (when needs-k (cons "_K" nil)))
                 (frame (if needs-k (list binder-cell k-cell) (list binder-cell))))
            (multiple-value-bind (push-item get-items)
                (make-bounded-heap (min limit (max 1 (value-size val))) greater-fn)
              (if (= sort-count 1)
                  (cond
                    ((and (value-is-list val) (value-storage val))
                     (let ((storage (value-storage val)))
                       (loop for i from 0 below (length storage)
                             for item = (svref storage i)
                             do (funcall push-item (make-sort-item item item i)))))
                    (t
                     (loop for (nil . item) in (aggregate-elements val)
                           for idx from 0
                           do (funcall push-item (make-sort-item item item idx)))))
                  (progn
                    (ctx-push-frame ctx frame)
                    (unwind-protect
                         (cond
                           ((and (value-is-list val) (value-storage val))
                            (let ((storage (value-storage val)))
                              (loop for i from 0 below (length storage)
                                    for item = (svref storage i)
                                    do (setf (cdr binder-cell) item)
                                       (when needs-k
                                         (setf (cdr k-cell) (format-index-text (1+ i))))
                                       (funcall push-item (make-sort-item item (args-eval a body) i)))))
                           ((value-shape val)
                            (let* ((shape (value-shape val))
                                   (storage (value-storage val))
                                   (keys (record-shape-keys shape)))
                              (loop for k in keys
                                    for i from 0
                                    for item = (svref storage i)
                                    do (setf (cdr binder-cell) item)
                                       (when needs-k
                                         (setf (cdr k-cell) (%text k)))
                                       (funcall push-item (make-sort-item item (args-eval a body) i)))))
                           (t
                            (loop for (k . item) in (aggregate-elements val)
                                  for idx from 0
                                  do (setf (cdr binder-cell) item)
                                     (when needs-k
                                       (setf (cdr k-cell) (%text k)))
                                     (funcall push-item (make-sort-item item (args-eval a body) idx)))))
                      (ctx-pop-frame ctx))))
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
                       collect (if fresh
                                   (sort-item-item x)
                                   (value-copy (sort-item-item x))))))))))))

(define-builtin "SORT" 1 3
  (lambda (a ctx) (do-sort a ctx "ASC"))
  :lazy t :binds t)

(define-builtin "SORT_DESC" 1 3
  (lambda (a ctx) (do-sort a ctx "DESC"))
  :lazy t :binds t)

(define-builtin "SORT_BY" 2 4
  (lambda (a ctx) (do-sort a ctx nil))
  :lazy t :binds t)

(define-builtin "TOP" 2 4
  (lambda (a ctx) (do-top-sort a ctx "ASC"))
  :lazy t :binds t)

(define-builtin "TOP_DESC" 2 4
  (lambda (a ctx) (do-top-sort a ctx "DESC"))
  :lazy t :binds t)

(define-builtin "TOP_BY" 3 5
  (lambda (a ctx) (do-top-sort a ctx nil))
  :lazy t :binds t)

(defstruct (group-entry (:constructor make-group-entry (key key-str)))
  (key nil)
  (key-str "" :type string)
  (rows '() :type list))

(defun eval-key-hash (v)
  ;; The hash must agree with VALUE-EQL, which compares text by spelling (a number
  ;; and the text spelt the same are one key, and a decimal cache warmed on one of
  ;; them by arithmetic changes nothing). So it hashes the spelling, never the
  ;; cache: hashing the decimal fields put `X` and `"5"` in different buckets once
  ;; `X * 1` had been evaluated (LISP-C1).
  (let ((k (value-kind v)))
    (case k
      (:text (logxor (sxhash k) (sxhash (value-scalar v))))
      (:bool
       (if (value-scalar v) 12345 67890))
      (:none 0)
      (t (sxhash k)))))

;; A scalar key is hashed by its scalar alone, but a list or record key -- whose
;; kind is NONE, hashed 0 above -- is walked, and the walk must meet the depth
;; cap as every other one does (spec §6.4; review 2026-09-25 HOST-07).
(defun bucket-key-hash (v)
  (if (zerop (value-size v)) (eval-key-hash v) (value-hash v)))

(defun bucket-key-text (key pos)
  (let ((v key))
    (when (eq (value-kind v) :none)
      (when (value-null-p v) (fail "E_NULL" "value is NULL" pos))
      (fail "E_NOT_TEXT" "a bucket key must be text or a number, got a list or record" pos))
    (as-text v pos)))

(defun do-bucket (a ctx)
  (let* ((val (snapshot-source (args-val a 0)))
         (src-fresh (node-fresh-p (args-node a 0))))
    ;; A scalar is one element (SPEC 7.3); only NULL and an empty list have none.
    (if (or (value-null-p val)
            (and (zerop (value-size val)) (eq (value-kind val) :none)))
        (make-list-value nil)
        (let* ((count (args-count a))
               (binder (if (= count 4) (args-symbol a 1) "_"))
               (key-node (cond ((= count 2) (args-node a 1))
                               ((= count 3) (args-node a 1))
                               (t (args-node a 2))))
               (agg-node (cond ((= count 2) nil)
                               ((= count 3) (args-node a 2))
                               (t (args-node a 3))))
               (groups-table (make-hash-table :test #'eql))
               (groups '())
               (needs-k (node-contains-var-p key-node "_K"))
               (binder-cell (cons binder nil))
               (k-cell (when needs-k (cons "_K" nil)))
               (frame (if needs-k (list binder-cell k-cell) (list binder-cell))))
          (ctx-push-frame ctx frame)
          (unwind-protect
               (flet ((process-item (item k idx)
                        (setf (cdr binder-cell) item)
                        (when needs-k
                          (setf (cdr k-cell) (if k (%text k) (format-index-text idx))))
                        (let* ((eval-key (args-eval a key-node))
                               ;; A bare bucket's key is an index key (spec §3.3):
                               ;; the scalar, verbatim, and refused the way indexing
                               ;; refuses it -- never collapsed onto a string that
                               ;; stands for every list, record or NULL. The
                               ;; projected spelling has no map to key and groups
                               ;; by identity instead.
                               (key-str (if (null agg-node)
                                            (bucket-key-text eval-key (node-pos key-node))
                                            ""))
                               ;; The bare spelling groups by the index key it has just
                               ;; been given (spec 3.3, 7.3): two keys with the same
                               ;; text are one group whatever their structure. The
                               ;; projected spelling groups by identity.
                               (h (if (null agg-node) (sxhash key-str) (bucket-key-hash eval-key)))
                               (bucket (gethash h groups-table))
                               (found (find-if (lambda (g)
                                                 (if (null agg-node)
                                                     (string= (group-entry-key-str g) key-str)
                                                     (value-eql (group-entry-key g) eval-key)))
                                               bucket)))
                          (if found
                              (push item (group-entry-rows found))
                              (let* ((new-g (make-group-entry eval-key key-str)))
                                (setf (group-entry-rows new-g) (list item))
                                (setf (gethash h groups-table) (cons new-g bucket))
                                (push new-g groups))))))
                 (cond
                   ((and (value-is-list val) (value-storage val))
                    (let ((storage (value-storage val)))
                      (loop for i from 0 below (length storage)
                            for item = (svref storage i)
                            do (process-item item nil (1+ i)))))
                   ((value-shape val)
                    (let* ((shape (value-shape val))
                           (storage (value-storage val))
                           (keys (record-shape-keys shape)))
                      (loop for k in keys
                            for i from 0
                            for item = (svref storage i)
                            do (process-item item k (1+ i)))))
                   (t
                    (loop for (k . item) in (aggregate-elements val)
                          for idx from 1
                          do (process-item item k idx)))))
            (ctx-pop-frame ctx))
          (setf groups (nreverse groups))
          (dolist (g groups)
            (setf (group-entry-rows g) (nreverse (group-entry-rows g))))
          (if (null agg-node)
              (let ((out (make-none)))
                (dolist (g groups)
                  (value-set out (group-entry-key-str g) (make-list-value (if src-fresh
                                                                (group-entry-rows g)
                                                                (mapcar #'value-copy (group-entry-rows g))))))
                out)
              (let ((out '())
                    (agg-binder-cell (cons binder nil))
                    (agg-k-cell (cons "_K" nil)))
                (let ((agg-frame (list agg-binder-cell agg-k-cell)))
                  (ctx-push-frame ctx agg-frame)
                  (unwind-protect
                       (dolist (g groups)
                         (setf (cdr agg-binder-cell) (make-list-value (group-entry-rows g))
                               (cdr agg-k-cell) (group-entry-key g))
                         (let ((res (args-eval a agg-node)))
                           (push (if (node-fresh-p agg-node) res (value-copy res (node-pos agg-node))) out)))
                    (ctx-pop-frame ctx)))
                (make-list-value (nreverse out))))))))

(define-builtin "BUCKET" 2 4
  (lambda (a ctx) (do-bucket a ctx))
  :lazy t :binds t)


