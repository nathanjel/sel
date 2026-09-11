;;;; The SEL value, used by the interpreter and by host code alike — there is
;;;; deliberately no second representation of state. See spec/SPEC.md §3.
;;;;
;;;; A value may have a scalar, children, both, or neither. TEXT holds a CL
;;;; string, which on this implementation is a sequence of code points, so every
;;;; length and position SEL reports falls out without a conversion layer. BIN
;;;; holds a vector of octets.
;;;;
;;;; Children are an ordered alist. Order is normative — it is observable through
;;;; INDEXES, JOIN, MAP, FILTER and the dump — and re-assigning an existing key
;;;; must keep its original position, which is why this is not a hash table.
;;;;
;;;; Values are mutable and referenced, as in the JS host, so assignment copies
;;;; explicitly (§5.7): two variables never share structure.

(in-package #:sel)

;;; Insertion order is normative, so the children are a list. Looking a key up
;;; would then be an ASSOC scan and appending an NCONC walk, which makes building
;;; an n-element list O(n²) — the JS and PHP hosts get ordered-plus-O(1) for free
;;; from a Map and from PHP's ordered hash array, and TAIL and INDEX are how this
;;; host gets the same.
;;;
;;; TAIL is the last cons of CHILDREN, so appending is O(1). INDEX maps a key to
;;; its cons cell and is built only once a value has enough children to be worth
;;; a hash table — almost every value in a program has none. COUNT is kept
;;; because LENGTH on a list is itself O(n).
(defconstant +index-threshold+ 16)

(defstruct (record-shape (:constructor %make-record-shape (keys key-map size)))
  (keys nil :type list)
  (key-map (make-hash-table :test #'equal) :type hash-table)
  (size 0 :type fixnum)
  (alias-cache (make-hash-table :test #'equal) :type hash-table))

(defvar *shape-cache* (make-hash-table :test #'equal))

(defun get-record-shape (keys)
  (let ((canonical (copy-list keys)))
    (or (gethash canonical *shape-cache*)
        (let* ((sz (length canonical))
               (map (make-hash-table :test #'equal :size (max 4 sz)))
               (idx 0))
          (dolist (k canonical)
            (setf (gethash k map) idx)
            (incf idx))
          (let ((shape (%make-record-shape canonical map sz)))
            (setf (gethash canonical *shape-cache*) shape)
            shape)))))

(defstruct (value (:constructor %make-value-raw (kind scalar children-internal tail count index is-list shape storage dec-val)))
  (kind :none :type keyword)     ; :none :text :bin :bool :thunk
  (scalar nil)
  (children-internal nil :type list)      ; list of (key . value), insertion-ordered
  (tail nil :type list)          ; last cons of CHILDREN
  (count 0 :type fixnum)
  (index nil)                    ; key -> cons cell, once COUNT reaches the threshold
  (is-list nil :type boolean)
  (shape nil)                    ; shared record-shape pointer
  (storage nil)                  ; simple-vector of values (records or lists)
  (dec-val nil))                 ; cached DEC struct for numeric text

(defun %make-value (kind scalar children &optional is-list shape storage dec-val)
  (%make-value-raw kind scalar children (last children) (length children) nil is-list shape storage dec-val))

(defun %make-shaped-value (shape storage)
  (%make-value-raw :none nil nil nil (record-shape-size shape) nil nil shape storage nil))

(defun %make-list-value-fast (storage)
  (%make-value-raw :none nil nil nil (length storage) nil t nil storage nil))

(defun ensure-shaped-children (v)
  (let ((shape (value-shape v)))
    (when (and shape (null (value-children-internal v)))
      (let* ((storage (value-storage v))
             (entries (loop for k in (record-shape-keys shape)
                            for i from 0
                            collect (cons k (svref storage i)))))
        (setf (value-children-internal v) entries
              (value-tail v) (last entries)
              (value-count v) (record-shape-size shape))
        (when (>= (value-count v) +index-threshold+)
          (setf (value-index v) (record-shape-key-map shape)))))))

(defun ensure-list-children (v)
  (when (and (value-is-list v) (value-storage v) (null (value-children-internal v)))
    (let* ((storage (value-storage v))
           (n (length storage))
           (entries '()))
      (loop for i from 1 to n
            for item across storage
            do (push (cons (format nil "~d" i) item) entries))
      (setf entries (nreverse entries)
            (value-children-internal v) entries
            (value-tail v) (last entries)
            (value-count v) n)
      (when (>= n +index-threshold+)
        (%build-index v)))))

(defun value-children (v)
  (force-value v)
  (ensure-shaped-children v)
  (ensure-list-children v)
  (value-children-internal v))

(defun (setf value-children) (val v)
  (setf (value-children-internal v) val))

(defun force-value (v)
  (when (and (value-p v) (eq (value-kind v) :thunk))
    (let ((real (funcall (value-scalar v))))
      (force-value real)
      (setf (value-kind v) (value-kind real)
            (value-scalar v) (value-scalar real)
            (value-children-internal v) (value-children-internal real)
            (value-tail v) (value-tail real)
            (value-count v) (value-count real)
            (value-index v) (value-index real)
            (value-is-list v) (value-is-list real)
            (value-shape v) (value-shape real)
            (value-storage v) (value-storage real)
            (value-dec-val v) (value-dec-val real))))
  v)

(defun make-thunk-value (fn)
  (%make-value :thunk fn nil))

(defun %value-with-children (kind scalar entries &optional is-list)
  "Build a value from an ordered list of (key . value) conses, wiring up the
tail, count and index that keep lookup and append O(1)."
  (let ((v (%make-value kind scalar entries is-list)))
    (setf (value-tail v) (last entries)
          (value-count v) (length entries))
    (when (>= (value-count v) +index-threshold+)
      (%build-index v))
    v))

(defun %build-index (v)
  (let ((idx (make-hash-table :test #'equal :size (* 2 (value-count v)))))
    (dolist (cell (value-children-internal v))
      (setf (gethash (car cell) idx) cell))
    (setf (value-index v) idx)))

(defun %value-cell (v key)
  "The cons cell for KEY, or NIL."
  (force-value v)
  (ensure-shaped-children v)
  (ensure-list-children v)
  (let ((idx (value-index v)))
    (if idx
        (gethash key idx)
        (assoc key (value-children-internal v) :test #'string=))))

(defun make-none () (%make-value :none nil nil nil))
(defun make-null () (%make-value :none nil nil nil))

(defun make-text (s)
  (unless (valid-utf8-string-p s)
    (fail "E_UTF8" "text carries an unpaired surrogate"))
  (%make-value :text s nil))

;;; Internal: the text is already known to be well formed, so skip the check.
(defun %text (s) (%make-value :text s nil))

(defun make-bin (bytes)
  (%make-value :bin (if (typep bytes '(vector (unsigned-byte 8)))
                        bytes
                        (octets-from-list (coerce bytes 'list)))
               nil))

(defun make-bool (b) (%make-value :bool (and b t) nil))

(defun make-num (d)
  "D is a DEC or a decimal string. A string is canonicalised: 007 becomes 7."
  (etypecase d
    (dec (let ((v (%text (dec-format d))))
           (setf (value-dec-val v) d)
           v))
    (string (let ((p (dec-parse d)))
              (unless p (fail "E_NOT_NUM" (format nil "not a number: ~a" d)))
              (let ((v (%text (dec-format p))))
                (setf (value-dec-val v) p)
                v)))))

(defun make-int (n)
  (let* ((d (dec-from-int n))
         (v (%text (dec-format d))))
    (setf (value-dec-val v) d)
    v))

(defun parse-list-key (k)
  (declare (type string k))
  (let ((len (length k)))
    (when (and (<= 1 len 9)
               (char<= #\1 (char k 0) #\9)
               (loop for i from 1 below len always (ascii-digit-p (char k i))))
      (parse-integer k))))

;;; Fast list value backed by simple-vector.
(defun make-list-value (values)
  (let ((vec (if (typep values 'simple-vector)
                 values
                 (coerce values 'simple-vector))))
    (%make-list-value-fast vec)))

;;; --- children --------------------------------------------------------------

;;; Kind predicates. The recommended way to branch on kind in every host,
;;; because it is the one spelling that reads the same in all four: the kind
;;; *values* are a keyword here, a string in JS, a class constant in PHP and an
;;; enum in C++, so only a predicate can be documented uniformly. These test the
;;; value's own kind and do not apply scalar context.
(defun value-none-p (v)
  (force-value v)
  (eq (value-kind v) :none))

(defun value-null-p (v)
  (force-value v)
  (and (eq (value-kind v) :none)
       (zerop (value-size v))
       (not (value-is-list v))))

(defun value-vacuous-p (v)
  (force-value v)
  (cond
    ((value-null-p v) t)
    ((and (eq (value-kind v) :none) (zerop (value-size v))) t)
    ((and (eq (value-kind v) :text) (zerop (value-size v)))
     (let ((s (value-scalar v)))
       (or (zerop (length s))
           (every (lambda (c) (member c '(#\Space #\Tab #\Return #\Newline))) s))))
    (t nil)))

(defun value-text-p (v)
  (force-value v)
  (eq (value-kind v) :text))

(defun value-bin-p (v)
  (force-value v)
  (eq (value-kind v) :bin))

(defun value-bool-p (v)
  (force-value v)
  (eq (value-kind v) :bool))

(defun value-size (v)
  (force-value v)
  (value-count v))

(defun value-has (v key)
  (force-value v)
  (cond
    ((value-shape v)
     (not (null (gethash key (record-shape-key-map (value-shape v))))))
    ((and (value-is-list v) (value-storage v))
     (let ((idx (parse-list-key key)))
       (and idx (<= 1 idx (length (value-storage v))))))
    (t
     (and (%value-cell v key) t))))

(defun value-get (v key)
  (force-value v)
  (cond
    ((value-shape v)
     (let ((idx (gethash key (record-shape-key-map (value-shape v)))))
       (when idx
         (force-value (svref (value-storage v) idx)))))
    ((and (value-is-list v) (value-storage v))
     (let ((idx (parse-list-key key)))
       (when (and idx (<= 1 idx (length (value-storage v))))
         (force-value (svref (value-storage v) (1- idx))))))
    (t
     (let ((cell (%value-cell v key)))
       (when cell
         (force-value (cdr cell)))))))

(defun value-keys (v)
  (force-value v)
  (cond
    ((value-shape v)
     (record-shape-keys (value-shape v)))
    ((and (value-is-list v) (value-storage v))
     (loop for i from 1 to (length (value-storage v))
           collect (format nil "~d" i)))
    (t
     (mapcar #'car (value-children v)))))

(defun value-values (v)
  (force-value v)
  (cond
    ((value-shape v)
     (let ((storage (value-storage v)))
       (loop for i from 0 below (length storage)
             collect (force-value (svref storage i)))))
    ((and (value-is-list v) (value-storage v))
     (let ((storage (value-storage v)))
       (loop for i from 0 below (length storage)
             collect (force-value (svref storage i)))))
    (t
     (mapcar (lambda (cell) (force-value (cdr cell))) (value-children v)))))

(defun value-entries (v)
  (force-value v)
  (ensure-shaped-children v)
  (ensure-list-children v)
  (dolist (cell (value-children-internal v))
    (force-value (cdr cell)))
  (value-children-internal v))

(defun value-set (v key child)
  "Re-assigning an existing key keeps its original position."
  (force-value v)
  (cond
    ((value-shape v)
     (let ((idx (gethash key (record-shape-key-map (value-shape v)))))
       (if idx
           (progn
             (setf (svref (value-storage v) idx) child)
             (when (value-children-internal v)
               (let ((cell (assoc key (value-children-internal v) :test #'string=)))
                 (when cell (setf (cdr cell) child)))))
           (progn
             (ensure-shaped-children v)
             (setf (value-shape v) nil
                   (value-storage v) nil)
             (value-set v key child)))))
    ((and (value-is-list v) (value-storage v))
     (ensure-list-children v)
     (setf (value-storage v) nil)
     (value-set v key child))
    (t
     (let ((cell (%value-cell v key)))
       (if cell
           (setf (cdr cell) child)
           (let ((new (list (cons key child))))
             (if (value-tail v)
                 (setf (cdr (value-tail v)) new)
                 (setf (value-children-internal v) new))
             (setf (value-tail v) new)
             (incf (value-count v))
             (let ((idx (value-index v)))
               (if idx
                   (setf (gethash key idx) (car new))
                   (when (>= (value-count v) +index-threshold+) (%build-index v)))))))))
  v)

;;; --- scalar context (§3.2) -------------------------------------------------

(defun scalar-source (v &optional at)
  "The value that supplies the scalar: V itself, or its first child, recursively."
  (let ((cur v)
        (guard 0))
    (force-value cur)
    (loop while (eq (value-kind cur) :none)
          do (when (value-null-p cur)
               (fail "E_NULL" "value is NULL" at))
             (when (zerop (value-size cur))
               (fail "E_NO_SCALAR" "value has no scalar and no children" at))
             (let ((first-val (cond
                                ((value-shape cur)
                                 (svref (value-storage cur) 0))
                                ((and (value-is-list cur) (value-storage cur))
                                 (svref (value-storage cur) 0))
                                (t
                                 (cdr (first (value-children-internal cur)))))))
               (setf cur (force-value first-val)))
             (incf guard)
             (when (> guard 1000)
               (fail "E_DEPTH" "scalar context nested too deeply" at)))
    cur))

(defun as-text (v &optional at)
  (let ((s (scalar-source v at)))
    (case (value-kind s)
      (:text (value-scalar s))
      (:bin (fail "E_NOT_TEXT" "expected text, got binary (use FROM_UTF8)" at))
      (t (fail "E_NOT_TEXT" "expected text, got boolean" at)))))

(defun as-bytes (v &optional at)
  (let ((s (scalar-source v at)))
    (case (value-kind s)
      (:bin (value-scalar s))
      (:text (encode-utf8 (value-scalar s) at))
      (t (fail "E_NOT_BIN" "expected binary or text, got boolean" at)))))

(defun as-bool (v &optional at)
  (let ((s (scalar-source v at)))
    (if (eq (value-kind s) :bool)
        (value-scalar s)
        (fail "E_NOT_BOOL" "expected a boolean — SEL has no truthiness" at))))

(defun as-dec (v &optional at)
  (let ((s (scalar-source v at)))
    (unless (eq (value-kind s) :text)
      (fail "E_NOT_NUM"
            (format nil "expected a number, got ~(~a~)" (value-kind s))
            at))
    (or (value-dec-val s)
        (let ((p (dec-parse (value-scalar s) at)))
          (unless p (fail "E_NOT_NUM" (format nil "not a number: ~s" (value-scalar s)) at))
          (setf (value-dec-val s) p)
          p))))

;;; The non-throwing probe, as ISNUM uses.
(defun looks-numeric (v)
  (force-value v)
  (if (and (eq (value-kind v) :none) (zerop (value-size v)))
      nil
      (handler-case
          (let ((s (scalar-source v)))
            (and (eq (value-kind s) :text)
                 (or (value-dec-val s)
                     (dec-parse (value-scalar s)))
                 t))
        (sel-error () nil))))

;;; --- copying ---------------------------------------------------------------

(defun value-copy (v &optional pos)
  "Assignment copies by value: two variables never share structure (§5.7)."
  (value-copy-at v 1 pos))

(defun value-copy-at (v depth pos)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (force-value v)
  (cond
    ((eq (value-kind v) :thunk)
     (%make-value :thunk (value-scalar v) nil))
    ((value-shape v)
     (let* ((shape (value-shape v))
            (n (record-shape-size shape))
            (old-storage (value-storage v))
            (new-storage (make-array n)))
       (loop for i from 0 below n
             do (setf (svref new-storage i)
                      (value-copy-at (svref old-storage i) (1+ depth) pos)))
       (%make-shaped-value shape new-storage)))
    ((and (value-is-list v) (value-storage v))
     (let* ((old-storage (value-storage v))
            (n (length old-storage))
            (new-storage (make-array n)))
       (loop for i from 0 below n
             do (setf (svref new-storage i)
                      (value-copy-at (svref old-storage i) (1+ depth) pos)))
       (%make-list-value-fast new-storage)))
    (t
     (let ((new-v (%value-with-children
                   (value-kind v)
                   (if (eq (value-kind v) :bin) (copy-seq (value-scalar v)) (value-scalar v))
                   (loop for (k . child) in (value-children v)
                         collect (cons k (value-copy-at child (1+ depth) pos)))
                   (value-is-list v))))
       (setf (value-dec-val new-v) (value-dec-val v))
       new-v))))

;;; --- structural equality (§5.4) --------------------------------------------

(defun value-eql (a b &optional pos)
  "Same kind, equal scalars with numbers *not* normalised, and children with the
same keys in the same order, pairwise EQL."
  (value-eql-at a b 1 pos))

(defun value-eql-at (a b depth pos)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (force-value a)
  (force-value b)
  (and (eq (value-kind a) (value-kind b))
       (case (value-kind a)
         (:text (string= (value-scalar a) (value-scalar b)))
         (:bin (bytes-equal (value-scalar a) (value-scalar b)))
         (:bool (eq (value-scalar a) (value-scalar b)))
         (t t))
       (= (value-size a) (value-size b))
       (cond
         ((and (value-shape a) (value-shape b)
               (eq (value-shape a) (value-shape b)))
          (let ((sa (value-storage a))
                (sb (value-storage b))
                (n (record-shape-size (value-shape a))))
            (loop for i from 0 below n
                  always (value-eql-at (svref sa i) (svref sb i) (1+ depth) pos))))
         ((and (value-is-list a) (value-is-list b)
               (value-storage a) (value-storage b))
          (let ((sa (value-storage a))
                (sb (value-storage b))
                (n (length sa)))
            (loop for i from 0 below n
                  always (value-eql-at (svref sa i) (svref sb i) (1+ depth) pos))))
         (t
          (loop for (ka . va) in (value-children a)
                for (kb . vb) in (value-children b)
                always (and (string= ka kb)          ; key order is normative
                            (value-eql-at va vb (1+ depth) pos)))))))

(defun value-hash (v &optional (depth 1))
  "Computes a fast structural hash for a SEL value."
  (when (> depth +max-depth+)
    (return-from value-hash 0))
  (force-value v)
  (let ((h (sxhash (value-kind v))))
    (case (value-kind v)
      (:text
       (let ((s (value-scalar v)))
         (when (stringp s)
           (setf h (logand most-positive-fixnum (logxor h (sxhash s)))))))
      (:bool
       (setf h (logand most-positive-fixnum (logxor h (if (value-scalar v) 12345 67890)))))
      (:bin
       (let ((b (value-scalar v)))
         (when (typep b 'vector)
           (setf h (logand most-positive-fixnum (logxor h (sxhash (length b)))))))))
    (cond
      ((value-shape v)
       (let* ((shape (value-shape v))
              (keys (record-shape-keys shape))
              (storage (value-storage v)))
         (loop for k in keys
               for i from 0
               do (setf h (logand most-positive-fixnum (logxor h (sxhash k))))
                  (setf h (logand most-positive-fixnum
                                  (logxor (ash (logand h #x1ffffffffffffff) 3)
                                          (value-hash (svref storage i) (1+ depth))))))))
      ((and (value-is-list v) (value-storage v))
       (let* ((storage (value-storage v))
              (n (length storage)))
         (loop for i from 0 below n
               do (setf h (logand most-positive-fixnum
                                  (logxor (ash (logand h #x1ffffffffffffff) 3)
                                          (value-hash (svref storage i) (1+ depth))))))))
      ((value-children-internal v)
       (dolist (pair (value-children-internal v))
         (setf h (logand most-positive-fixnum (logxor h (sxhash (car pair)))))
         (setf h (logand most-positive-fixnum
                         (logxor (ash (logand h #x1ffffffffffffff) 3)
                                 (value-hash (cdr pair) (1+ depth))))))))
    h))

;;; --- canonical dump (conformance/README.md) --------------------------------

(defun quote-dump (s)
  "The dump's escape set: backslash, quote, the three whitespace escapes, and
\\uXXXX for anything else below U+0020."
  (with-output-to-string (out)
    (write-char #\" out)
    (loop for ch across s
          for c = (char-code ch)
          do (case ch
               (#\\ (write-string "\\\\" out))
               (#\" (write-string "\\\"" out))
               (#\Newline (write-string "\\n" out))
               (#\Tab (write-string "\\t" out))
               (#\Return (write-string "\\r" out))
               (t (if (< c #x20)
                      (format out "\\u~(~4,'0x~)" c)
                      (write-char ch out)))))
    (write-char #\" out)))

(defun value-dump (v)
  (value-dump-at v 1))

(defun value-dump-at (v depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  (force-value v)
  (let ((s (case (value-kind v)
             (:none "-")
             (:text (concatenate 'string "t" (quote-dump (value-scalar v))))
             (:bin (concatenate 'string "b" (bytes-to-hex (value-scalar v))))
             (:bool (if (value-scalar v) "TRUE" "FALSE")))))
    (if (zerop (value-size v))
        s
        (concatenate 'string s "{"
                     (format nil "~{~a~^, ~}"
                             (cond
                               ((value-shape v)
                                (let* ((shape (value-shape v))
                                       (keys (record-shape-keys shape))
                                       (storage (value-storage v)))
                                  (loop for k in keys
                                        for i from 0
                                        collect (concatenate 'string (quote-dump k) "="
                                                             (value-dump-at (svref storage i) (1+ depth))))))
                               ((and (value-is-list v) (value-storage v))
                                (ensure-list-children v)
                                (loop for (k . child) in (value-children-internal v)
                                      collect (concatenate 'string (quote-dump k) "="
                                                           (value-dump-at child (1+ depth)))))
                               (t
                                (loop for (k . child) in (value-children v)
                                      collect (concatenate 'string (quote-dump k) "="
                                                           (value-dump-at child (1+ depth)))))))
                     "}"))))

;;; --- host convenience ------------------------------------------------------

(defun from-native (x)
  "Convert CL data to a SEL value. Floats are refused outright: they have no
exact decimal form, and SEL has no floating point. Pass a string instead."
  (from-native-at x 1))

(defun from-native-at (x depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  (etypecase x
    (null (make-none))
    (value x)
    ((member t) (make-bool t))
    (string (make-text x))
    (integer (make-int x))
    (ratio (error "~a has no exact decimal form; pass a decimal string instead" x))
    (float (error "floats have no exact decimal form; pass a decimal string instead"))
    ((vector (unsigned-byte 8)) (make-bin x))
    ;; A plain list is a SEL list, keyed from 1 — so ITEMS[1] means the first
    ;; line on every host. An alist is a keyed value.
    (cons (if (and (consp (first x)) (stringp (car (first x))))
              (let* ((keys (mapcar #'car x))
                     (n (length keys)))
                (if (= (length (remove-duplicates keys :test #'string=)) n)
                    (let* ((shape (get-record-shape keys))
                           (storage (make-array n)))
                      (loop for (nil . val) in x
                            for idx from 0
                            do (setf (svref storage idx) (from-native-at val (1+ depth))))
                      (%make-shaped-value shape storage))
                    (let ((v (make-none)))
                      (loop for (k . val) in x
                            do (value-set v k (from-native-at val (1+ depth))))
                      v)))
              (make-list-value (mapcar (lambda (e) (from-native-at e (1+ depth))) x))))))

(defun to-native (v)
  "The inverse of FROM-NATIVE, near enough for reporting: a bare scalar when the
value has no children, otherwise an alist, with the scalar under \"_\"."
  (to-native-at v 1))

(defun to-native-at (v depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  (force-value v)
  (let ((scalar (case (value-kind v)
                  ((:text :bin :bool) (value-scalar v))
                  (t nil))))
    (if (zerop (value-size v))
        scalar
        (let ((entries (cond
                         ((value-shape v)
                          (let* ((shape (value-shape v))
                                 (keys (record-shape-keys shape))
                                 (storage (value-storage v)))
                            (loop for k in keys
                                  for i from 0
                                  collect (cons k (to-native-at (svref storage i) (1+ depth))))))
                         ((and (value-is-list v) (value-storage v))
                          (let ((storage (value-storage v))
                                (n (length (value-storage v))))
                            (loop for i from 1 to n
                                  collect (cons (format nil "~d" i) (to-native-at (svref storage (1- i)) (1+ depth))))))
                         (t
                          (loop for (k . child) in (value-children v)
                                collect (cons k (to-native-at child (1+ depth))))))))
          (if (and (null scalar) (not (eq (value-kind v) :bool)))
              entries
              (cons (cons "_" scalar) entries))))))
