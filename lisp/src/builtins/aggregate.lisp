;;;; Aggregates. These are why SEL needs no loop: each evaluates one argument
;;;; node once per element, which is the same move IF makes, repeated.

(in-package #:sel)

;;; Runs VISIT per element with the binder and _K in scope. A non-NIL return from
;;; VISIT stops the walk and becomes the result.
(defun aggregate-walk (a ctx visit)
  (let* ((three (= (args-count a) 3))
         (binder (if three (args-symbol a 1) "_"))
         (body (args-node a (if three 2 1))))
    (loop for (key . item) in (aggregate-elements (args-val a 0))
          do (ctx-push-frame ctx (list (cons binder item)
                                       (cons "_K" (%text key))))
             (let ((result
                     (unwind-protect
                          (funcall visit (args-eval a body) key item body)
                       (ctx-pop-frame ctx))))
               (when result (return result))))))

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
                        (push (value-copy r) out)
                        nil))
      (make-list-value (nreverse out))))
  :lazy t :binds t)

;;; The one aggregate that preserves keys — a filtered list should still be
;;; addressable the way the original was.
(define-builtin "FILTER" 2 3
  (lambda (a ctx)
    (let ((out (make-list-value nil)))
      (aggregate-walk a ctx
                      (lambda (r key item body)
                        (when (as-bool r (node-pos body))
                          (value-set out key (value-copy item)))
                        nil))
      out))
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

;;; Strict, not an aggregate: its second argument is a separator, not a body.
(define-builtin "JOIN" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((sep (args-text a 1))
          (at (args-pos-of a 0)))
      (%text (with-output-to-string (out)
               (loop for (key . item) in (aggregate-elements (args-val a 0))
                     for first = t then nil
                     do (progn key)
                        (unless first (write-string sep out))
                        (write-string (as-text item at) out)))))))

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

(defun do-sort (a ctx forced-dir)
  (let ((val (args-val a 0)))
    (if (value-null-p val)
        (make-list-value nil)
        (let ((ents (aggregate-elements val)))
          (if (null ents)
              (make-list-value nil)
              (let* ((count (args-count a))
                     direction
                     binder
                     body
                     indexed)
                (if (= count 1)
                    (progn
                      (setf direction (or forced-dir "ASC"))
                      (setf indexed
                            (loop for (nil . item) in ents
                                  for idx from 0
                                  collect (list :item item :key item :idx idx))))
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
                                (args-pos-of a pos-idx))))
                      (setf indexed
                            (loop for (k . item) in ents
                                  for idx from 0
                                  collect
                                  (progn
                                    (ctx-push-frame ctx (list (cons binder item)
                                                             (cons "_K" (%text k))))
                                    (let ((eval-key
                                            (unwind-protect
                                                 (args-eval a body)
                                              (ctx-pop-frame ctx))))
                                      (list :item item :key eval-key :idx idx)))))))
                (let ((desc (string= direction "DESC")))
                  (setf indexed
                        (stable-sort indexed
                                     (lambda (x y)
                                       (let ((c (compare-values (getf x :key) (getf y :key))))
                                         (when desc (setf c (- c)))
                                         (< c 0))))))
                (make-list-value
                 (loop for x in indexed
                       collect (value-copy (getf x :item))))))))))

(define-builtin "SORT" 1 3
  (lambda (a ctx) (do-sort a ctx "ASC"))
  :lazy t :binds t)

(define-builtin "SORT_DESC" 1 3
  (lambda (a ctx) (do-sort a ctx "DESC"))
  :lazy t :binds t)

(define-builtin "SORT_BY" 2 4
  (lambda (a ctx) (do-sort a ctx nil))
  :lazy t :binds t)

(defun do-group-by (a ctx)
  (let* ((val (args-val a 0)))
    (if (value-null-p val)
        (make-list-value nil)
        (let ((ents (aggregate-elements val)))
          (if (null ents)
              (make-list-value nil)
              (let* ((count (args-count a))
                     (binder (if (= count 4) (args-symbol a 1) "_"))
                     (key-node (cond ((= count 2) (args-node a 1))
                                     ((= count 3) (args-node a 1))
                                     (t (args-node a 2))))
                     (agg-node (cond ((= count 2) nil)
                                     ((= count 3) (args-node a 2))
                                     (t (args-node a 3))))
                     (groups '()))
                (loop for (k . item) in ents
                      for idx from 1
                      do (ctx-push-frame ctx (list (cons binder item)
                                                   (cons "_K" (%text (format nil "~d" idx)))))
                         (let ((eval-key (unwind-protect
                                              (args-eval a key-node)
                                           (ctx-pop-frame ctx))))
                           (let ((found (find-if (lambda (g) (value-eql (getf g :key) eval-key)) groups)))
                             (if found
                                 (setf (getf found :rows) (append (getf found :rows) (list (value-copy item))))
                                 (let ((key-str (cond ((eq (value-kind eval-key) :text)
                                                       (value-scalar eval-key))
                                                      ((eq (value-kind eval-key) :bool)
                                                       (if (value-scalar eval-key) "TRUE" "FALSE"))
                                                      ((looks-numeric eval-key)
                                                       (value-scalar eval-key))
                                                      (t ""))))
                                   (push (list :key (value-copy eval-key)
                                               :key-str key-str
                                               :rows (list (value-copy item)))
                                         groups))))))
                (setf groups (nreverse groups))
                (if (null agg-node)
                    (let ((out (make-none)))
                      (dolist (g groups)
                        (value-set out (getf g :key-str) (make-list-value (getf g :rows))))
                      out)
                    (let ((out '()))
                      (dolist (g groups)
                        (ctx-push-frame ctx (list (cons binder (make-list-value (getf g :rows)))
                                                  (cons "_K" (value-copy (getf g :key)))))
                        (let ((res (unwind-protect
                                        (args-eval a agg-node)
                                     (ctx-pop-frame ctx))))
                          (push (value-copy res) out)))
                      (make-list-value (nreverse out))))))))))

(define-builtin "GROUP_BY" 2 4
  (lambda (a ctx) (do-group-by a ctx))
  :lazy t :binds t)

