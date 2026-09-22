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

(defvar *index-string-cache*
  (let ((vec (make-array 10001 :initial-element nil)))
    (loop for i from 1 to 10000
          do (setf (aref vec i) (format nil "~d" i)))
    vec))

(defvar *index-text-cache*
  (let ((vec (make-array 10001 :initial-element nil)))
    (loop for i from 1 to 10000
          do (setf (aref vec i) (%text (svref *index-string-cache* i))))
    vec))

(declaim (inline format-index-string format-index-text))
(defun format-index-string (n)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum n))
  (if (and (<= 1 n) (<= n 10000))
      (svref (the simple-vector *index-string-cache*) n)
      (format nil "~d" n)))

(defun format-index-text (n)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum n))
  (if (and (<= 1 n) (<= n 10000))
      (svref (the simple-vector *index-text-cache*) n)
      (%text (format nil "~d" n))))

;;; Runs VISIT per element with the binder and _K in scope. A non-NIL return from
;;; VISIT stops the walk and becomes the result.
(defun aggregate-walk (a ctx visit &optional (pass-key nil) (tentative nil) (body-override nil))
  "Runs VISIT per element with the binder and _K in scope. A TENTATIVE body (a
conjunct the physical optimizer moved under a LINK, SEL-0051) that raises does
not decide the element: only FILTER walks tentatively, and for it a raise means
keep -- the visitor sees T and the FILTER after the LINK decides."
  (let* ((three (= (args-count a) 3))
         (binder (if three (args-symbol a 1) "_"))
         (body (or body-override (args-node a (if three 2 1))))
         (val (args-val a 0)))
    (unless (or (value-null-p val)
                (and (eq (value-kind val) :none) (zerop (value-size val))))
      (let* ((needs-k (node-contains-var-p body "_K"))
             (binder-cell (cons binder nil))
             (k-cell (when needs-k (cons "_K" nil)))
             (frame (if needs-k (list binder-cell k-cell) (list binder-cell))))
        (ctx-push-frame ctx frame)
        (flet ((eval-body ()
                 (if tentative
                     (handler-case (args-eval a body)
                       (sel-error ()
                         (incf (context-tentative-kept ctx))
                         (make-bool t)))
                     (args-eval a body))))
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

(define-builtin "MAP" 2 3
  (lambda (a ctx)
    (let ((out '()))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (declare (ignore key item body))
                        (push r out)
                        nil))
      (make-list-value (nreverse out))))
  :lazy t :binds t)

;;; The one aggregate that preserves keys — a filtered list should still be
;;; addressable the way the original was.
(define-builtin "FILTER" 2 3
  (lambda (a ctx)
   (block filter-body
    (let* ((pairs '())
           (written (args-node a (1- (args-count a))))
           (tentative (node-tentative written))
           ;; A predicate whose leading conjuncts were pushed under the LINK
           ;; below: when no tentative body kept a row on an error while the
           ;; source ran, every row here passed them, and only the remaining
           ;; conjuncts are evaluated (TRUE when there are none); otherwise
           ;; the whole predicate, as written, decides -- and raises -- in
           ;; the source's order.
           (kept-before (context-tentative-kept ctx))
           (body-override (progn
                            (args-val a 0)
                            (when (and (node-pushed-down written)
                                       (= (context-tentative-kept ctx) kept-before))
                              (node-remaining written)))))
      ;; Nothing remains: every row of the join below passed, and the join
      ;; built a fresh list this FILTER would only copy.
      (when (and body-override (eq (node-kind body-override) :bool) (node-b body-override))
        (return-from filter-body (args-val a 0)))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (when (if tentative
                                  (handler-case (as-bool r (node-pos body))
                                    (sel-error ()
                                      (incf (context-tentative-kept ctx))
                                      t))
                                  (as-bool r (node-pos body)))
                          (push (cons key item) pairs))
                        nil)
                      t tentative body-override)
      (if (null pairs)
          (%make-value :none nil nil t)
          (let ((entries (nreverse pairs)))
            (%value-with-children :none nil entries t))))))
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
    (let ((sep (args-text a 1))
          (at (args-pos-of a 0))
          (val (args-val a 0)))
      (%text (with-output-to-string (out)
               (cond
                 ((and (value-is-list val) (value-storage val))
                  (let ((storage (value-storage val)))
                    (loop for i from 0 below (length storage)
                          for first = t then nil
                          do (unless first (write-string sep out))
                             (write-string (as-text (svref storage i) at) out))))
                 (t
                  (loop for (key . item) in (aggregate-elements val)
                        for first = t then nil
                        do (progn key)
                           (unless first (write-string sep out))
                           (write-string (as-text item at) out)))))))))

(defun compare-values (a b)
  (let ((a-null (value-null-p a))
        (b-null (value-null-p b)))
    (cond
      ((and a-null b-null) 0)
      (a-null -1)
      (b-null 1)
      (t
       (let ((a-num (looks-numeric a))
             (b-num (looks-numeric b)))
         (cond
           ((and a-num b-num)
            (dec-cmp (as-dec a) (as-dec b)))
           ((and (eq (value-kind a) :bool) (eq (value-kind b) :bool))
            (let ((av (if (value-scalar a) 1 0))
                  (bv (if (value-scalar b) 1 0)))
              (cond ((< av bv) -1) ((> av bv) 1) (t 0))))
           ((and (member (value-kind a) '(:text :bin))
                 (member (value-kind b) '(:text :bin)))
            (bytes-compare (as-bytes a) (as-bytes b)))
           (t
            (flet ((rank (v)
                     (cond
                       ((value-null-p v) 0)
                       ((eq (value-kind v) :bool) 1)
                       ((looks-numeric v) 2)
                       ((eq (value-kind v) :text) 3)
                       ((eq (value-kind v) :bin) 4)
                       (t 5))))
              (let ((ra (rank a))
                    (rb (rank b)))
                (cond ((< ra rb) -1) ((> ra rb) 1) (t 0)))))))))))

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

(defstruct (sort-item (:constructor make-sort-item (item key idx)))
  item
  key
  (idx 0 :type fixnum))

(defun do-sort (a ctx forced-dir)
  (let ((val (args-val a 0)))
    (if (or (value-null-p val) (zerop (value-size val)))
        (make-list-value nil)
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
                            direction (string-upcase (args-text a 2))))
                     ((args-symbol-p a 1)
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction "ASC"))
                     (t
                      (setf binder "_"
                            body (args-node a 1)
                            direction (string-upcase (args-text a 2))))))
                  (t ; 4
                   (setf binder (args-symbol a 1)
                         body (args-node a 2)
                         direction (string-upcase (args-text a 3)))))
                (unless (or (string= direction "ASC") (string= direction "DESC"))
                  (let ((pos-idx (if (= count 4) 3 2)))
                    (fail "E_BAD_ARG" "sort direction must be 'ASC' or 'DESC'"
                          (args-pos-of a pos-idx))))))
          (let* ((desc (string= direction "DESC"))
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
                                 collect (make-sort-item item item i)))))
                  (t
                   (setf indexed
                         (loop for (nil . item) in (aggregate-elements val)
                               for idx from 0
                               collect (make-sort-item item item idx)))))
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
                                        collect (make-sort-item item (args-eval a body) i)))))
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
                                        collect (make-sort-item item (args-eval a body) i)))))
                         (t
                          (setf indexed
                                (loop for (k . item) in (aggregate-elements val)
                                      for idx from 0
                                      do (setf (cdr binder-cell) item)
                                         (when needs-k
                                           (setf (cdr k-cell) (%text k)))
                                      collect (make-sort-item item (args-eval a body) idx)))))
                    (ctx-pop-frame ctx))))
            (setf indexed
                  (stable-sort indexed
                               (lambda (x y)
                                 (let ((c (compare-values (sort-item-key x) (sort-item-key y))))
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
         (limit (args-non-neg-int a (1- count))))
    (if (or (zerop limit) (value-null-p val) (zerop (value-size val)))
        (make-list-value nil)
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
                            direction (string-upcase (args-text a 2))))
                     ((args-symbol-p a 1)
                      (setf binder (args-symbol a 1)
                            body (args-node a 2)
                            direction "ASC"))
                     (t
                      (setf binder "_"
                            body (args-node a 1)
                            direction (string-upcase (args-text a 2))))))
                  (t ; 4
                   (setf binder (args-symbol a 1)
                         body (args-node a 2)
                         direction (string-upcase (args-text a 3)))))
                (unless (or (string= direction "ASC") (string= direction "DESC"))
                  (let ((pos-idx (if (= sort-count 4) 3 2)))
                    (fail "E_BAD_ARG" "sort direction must be 'ASC' or 'DESC'"
                          (args-pos-of a pos-idx))))))
          (let* ((desc (string= direction "DESC"))
                 (greater-fn (if desc
                                 (lambda (x y)
                                   (let ((c (compare-values (sort-item-key x) (sort-item-key y))))
                                     (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (< c 0))))
                                 (lambda (x y)
                                   (let ((c (compare-values (sort-item-key x) (sort-item-key y))))
                                     (if (zerop c) (> (sort-item-idx x) (sort-item-idx y)) (> c 0))))))
                 (needs-k (and body (node-contains-var-p body "_K")))
                 (binder-cell (cons binder nil))
                 (k-cell (when needs-k (cons "_K" nil)))
                 (frame (if needs-k (list binder-cell k-cell) (list binder-cell))))
            (multiple-value-bind (push-item get-items)
                (make-bounded-heap limit greater-fn)
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
                                     (funcall push-item (make-sort-item item (args-eval a body) idx))))))
                      (ctx-pop-frame ctx)))
              (let ((items (funcall get-items)))
                (setf items (stable-sort items
                                         (lambda (x y)
                                           (let ((c (compare-values (sort-item-key x) (sort-item-key y))))
                                             (if (zerop c)
                                                 (< (sort-item-idx x) (sort-item-idx y))
                                                 (progn
                                                   (when desc (setf c (- c)))
                                                   (< c 0)))))))
                (make-list-value
                 (loop for x in items
                       collect (sort-item-item x))))))))))

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
  (let ((k (value-kind v)))
    (case k
      (:text
       (let ((dec (value-dec-val v)))
         (if dec
             (logxor (sxhash k)
                     (if (dec-neg dec) 1 0)
                     (sxhash (dec-scale dec))
                     (sxhash (dec-digits dec)))
             (let ((s (value-scalar v)))
               (if (stringp s)
                   (logxor (sxhash k) (sxhash s))
                   (sxhash k))))))
      (:bool
       (if (value-scalar v) 12345 67890))
      (:none 0)
      (t (sxhash k)))))

(defun bucket-key-text (key pos)
  (let ((v key))
    (when (eq (value-kind v) :none)
      (when (value-null-p v) (fail "E_NULL" "value is NULL" pos))
      (fail "E_NOT_TEXT" "a bucket key must be text or a number, got a list or record" pos))
    (as-text v pos)))

(defun do-bucket (a ctx)
  (let* ((val (args-val a 0)))
    (if (or (value-null-p val) (zerop (value-size val)))
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
                               (h (eval-key-hash eval-key))
                               (bucket (gethash h groups-table))
                               (found (find-if (lambda (g) (value-eql (group-entry-key g) eval-key)) bucket)))
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
                  (value-set out (group-entry-key-str g) (make-list-value (mapcar #'value-copy (group-entry-rows g)))))
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
                           (push res out)))
                    (ctx-pop-frame ctx)))
                (make-list-value (nreverse out))))))))

(define-builtin "BUCKET" 2 4
  (lambda (a ctx) (do-bucket a ctx))
  :lazy t :binds t)


