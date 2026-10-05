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
  (size 0 :type fixnum))

(defvar *shape-cache* (make-hash-table :test #'equal :synchronized t))
(defvar *shape-lock* (sb-thread:make-mutex :name "sel record shape cache"))
(defconstant +shape-cache-entries+ 256)
(defconstant +shape-cache-max-keys+ 256)
(defconstant +shape-cache-max-chars+ 16384)

(defconstant +index-cache-size+ 10000
  "List positions 1..this have their key string (and, in aggregate.lisp, their
text value) made once and shared.")

(defvar *index-string-cache*
  (let ((vec (make-array (1+ +index-cache-size+) :initial-element nil)))
    (loop for i from 1 to +index-cache-size+
          do (setf (aref vec i) (format nil "~d" i)))
    vec)
  "The canonical key strings \"1\" .. \"10000\" of a list's positions, made once: a
`(format nil \"~d\" i)` per element was about 133 ns each, charged on every list
key, hash and flatten.")

(declaim (inline format-index-string))
(defun format-index-string (n)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum n))
  (if (and (<= 1 n) (<= n +index-cache-size+))
      (svref (the simple-vector *index-string-cache*) n)
      (format nil "~d" n)))

(defun keys-distinct-p (keys)
  "True when no two of the strings KEYS are equal. The quadratic REMOVE-DUPLICATES
this replaces took 7.7 s over 16,000 RECORD keys; a few keys are
compared pairwise, many through a hash table."
  (if (< (length keys) 24)
      (loop for tail on keys never (member (car tail) (cdr tail) :test #'string=))
      (let ((seen (make-hash-table :test 'equal :size (length keys))))
        (dolist (k keys t)
          (when (gethash k seen) (return nil))
          (setf (gethash k seen) t)))))

(defun get-record-shape (keys)
  ;; Probe with the caller's list; copy only when constructing a new layout.
  (or (gethash keys *shape-cache*)
      (let* ((canonical (copy-list keys))
             (sz (length canonical))
             (map (make-hash-table :test #'equal :size (max 4 sz)))
             (idx 0))
        (dolist (k canonical)
          (setf (gethash k map) idx)
          (incf idx))
        (let ((shape (%make-record-shape canonical map sz)))
          (when (and (<= sz +shape-cache-max-keys+)
                     (<= (loop for key in canonical sum (length key))
                         +shape-cache-max-chars+))
            (sb-thread:with-mutex (*shape-lock*)
              (when (>= (hash-table-count *shape-cache*) +shape-cache-entries+)
                (clrhash *shape-cache*))
              (setf (gethash canonical *shape-cache*) shape)))
          shape))))

(defstruct (value (:constructor %make-value-raw (kind %scalar children-internal tail count index is-list shape storage dec-val)))
  (kind :none :type keyword)     ; :none :text :bin :bool
  (%scalar nil)
  (children-internal nil :type list)      ; list of (key . value), insertion-ordered
  (tail nil :type list)          ; last cons of CHILDREN
  (count 0 :type fixnum)
  (index nil)                    ; key -> cons cell, once COUNT reaches the threshold
  (is-list nil :type boolean)
  (shape nil)                    ; shared record-shape pointer
  (storage nil)                  ; simple-vector of values (records or lists)
  (dec-val nil))                 ; cached DEC struct for numeric text

(declaim (inline value-scalar))
(defun value-scalar (v)
  (or (value-%scalar v)
      (let ((dec (value-dec-val v)))
        (if dec
            (let ((formatted (dec-format dec)))
              (setf (value-%scalar v) formatted)
              formatted)
            nil))))

(defun %make-value (kind scalar children &optional is-list)
  (%make-value-raw kind scalar children (last children) (length children) nil is-list nil nil nil))

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
        ;; A PRIVATE index, key -> cons cell. The shape's own key map is key ->
        ;; storage position and is shared by every value of that shape; using
        ;; it here answered %value-cell with an integer, and adding a key to a
        ;; 16-field record wrote its cell into the shared map, so the next record
        ;; built from those 16 keys inherited a key it never had.
        (when (>= (value-count v) +index-threshold+)
          (%build-index v))))))

(defun ensure-list-children (v)
  (when (and (value-is-list v) (value-storage v) (null (value-children-internal v)))
    (let* ((storage (value-storage v))
           (n (length storage))
           (entries '()))
      (loop for i from 1 to n
            for item across storage
            do (push (cons (format-index-string i) item) entries))
      (setf entries (nreverse entries)
            (value-children-internal v) entries
            (value-tail v) (last entries)
            (value-count v) n)
      (when (>= n +index-threshold+)
        (%build-index v)))))

(defun value-children (v)
  (ensure-shaped-children v)
  (ensure-list-children v)
  (value-children-internal v))

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
  (ensure-shaped-children v)
  (ensure-list-children v)
  (let ((idx (value-index v)))
    (if idx
        (gethash key idx)
        (assoc key (value-children-internal v) :test #'string=))))

(defun make-none () (%make-value :none nil nil nil))
(defun make-null () (%make-value :none nil nil nil))

;;; The host boundary (spec §8): a constructor
;;; checks what it is given and keeps a COPY -- SBCL strings and octet vectors
;;; are mutable, and a caller that changed one afterwards changed the value (a
;;; mutated key left the record unable to find it under either spelling).
;;; A constructor called with something it does not take (spec §8): E_BAD_ARG,
;;; a SEL-ERROR like every other boundary failure, never a CL TYPE-ERROR.
(defun bad-arg (control &rest args)
  (fail "E_BAD_ARG" (apply #'format nil control args)))

(defun make-text (s)
  (unless (stringp s) (bad-arg "text must be a string, not ~(~a~)" (type-of s)))
  (unless (valid-utf8-string-p s)
    (fail "E_UTF8" "text carries an unpaired surrogate"))
  (%make-value :text (copy-seq s) nil))

;;; Internal: the text is already known to be well formed, so skip the check.
(defun %text (s) (%make-value :text s nil))

(defun make-bin (bytes)
  (unless (typep bytes 'sequence) (bad-arg "bytes must be a sequence, not ~(~a~)" (type-of bytes)))
  (%make-value :bin (if (typep bytes '(vector (unsigned-byte 8)))
                        (coerce (copy-seq bytes) '(simple-array (unsigned-byte 8) (*)))
                        (let ((items (coerce bytes 'list)))
                          (dolist (x items)
                            (unless (and (integerp x) (<= 0 x 255))
                              (fail "E_RANGE" (format nil "byte ~a is not a whole number from 0 to 255" x))))
                          (octets-from-list items)))
               nil))

(defun make-bool (b) (%make-value :bool (and b t) nil))

(defun make-num (d)
  "D is a DEC or a decimal string. A string is canonicalised: 007 becomes 7.
A DEC is checked like one: well formed, within the digit caps, and canonical --
a negative zero loses its sign (spec §8)."
  (typecase d
    (dec (unless (and (>= (dec-digits d) 0) (>= (dec-scale d) 0))
           (bad-arg "not a decimal: the digits and the scale must be non-negative"))
         (dec-guard d nil)
         (%make-value-raw :text nil nil nil 0 nil nil nil nil
                          (if (and (zerop (dec-digits d)) (dec-neg d)) (dec-make nil 0 (dec-scale d)) d)))
    (string (let ((p (dec-parse d)))
              (unless p (fail "E_NOT_NUM" (format nil "not a number: ~a" d)))
              (%make-value-raw :text nil nil nil 0 nil nil nil nil p)))
    (t (bad-arg "not a number: expected a decimal string or a DEC, not ~(~a~)" (type-of d)))))

(defvar *int-cap* nil "10^MAX_INT_DIGITS, built on first use.")
(defvar *int-guard-bits* nil
  "The bit length below which an integer is surely under the digit cap: the bit
length of 10^(MAX_INT_DIGITS-1), less one. Integer arithmetic only -- a
floating constant here was the one float in the numeric core, and it had to be
kept in step with the limit by hand.")

(defun int-guard-bits ()
  (or *int-guard-bits*
      (setf *int-guard-bits* (1- (integer-length (expt 10 (1- +max-int-digits+)))))))

(defun make-int (n)
  (unless (integerp n) (bad-arg "not a whole number: ~a" n))
  ;; A native integer obeys the digit cap like the same digits in source (spec
  ;; §6.4); the bit-length test keeps an ordinary integer off the bignum compare.
  (when (and (> (integer-length n) 64)          ; nothing under 20 digits can trip the cap
             (> (integer-length n) (int-guard-bits))
             (>= (abs n) (or *int-cap* (setf *int-cap* (expt 10 +max-int-digits+)))))
    (fail "E_RANGE" (format nil "number has more than ~D integer digits" +max-int-digits+)))
  (let ((d (dec-from-int n)))
    (%make-value-raw :text nil nil nil 0 nil nil nil nil d)))

(defun parse-list-key (k)
  (declare (type string k))
  (let ((len (length k)))
    (when (and (<= 1 len 9)
               (char<= #\1 (char k 0) #\9)
               (loop for i from 1 below len always (ascii-digit-p (char k i))))
      (parse-integer k))))

;;; Fast list value backed by simple-vector.
;;; A list keeps no structure of the caller's: a vector is copied as a list is
;;; by COERCE (spec §8).
(defun make-list-value (values)
  (unless (typep values 'sequence) (bad-arg "a list is built from a sequence of values, not ~(~a~)" (type-of values)))
  (let ((vec (if (typep values 'simple-vector)
                 (copy-seq values)
                 (coerce values 'simple-vector))))
    (loop for x across vec
          unless (typep x 'value) do (bad-arg "a list is built from values, not ~(~a~)" (type-of x)))
    (%make-list-value-fast vec)))

;;; --- children --------------------------------------------------------------

;;; Kind predicates. The recommended way to branch on kind in every host,
;;; because it is the one spelling that reads the same in all four: the kind
;;; *values* are a keyword here, a string in JS, a class constant in PHP and an
;;; enum in C++, so only a predicate can be documented uniformly. These test the
;;; value's own kind and do not apply scalar context.
(defun value-none-p (v)
  (eq (value-kind v) :none))

(defun value-null-p (v)
  (and (eq (value-kind v) :none)
       (zerop (value-size v))
       (not (value-is-list v))))

(defun value-vacuous-p (v)
  (cond
    ((and (eq (value-kind v) :none) (zerop (value-size v))) t)
    ((and (eq (value-kind v) :text) (zerop (value-size v)))
     (let ((s (value-scalar v)))
       (or (zerop (length s))
           (every #'ascii-space-p s))))
    (t nil)))

(defun value-text-p (v)
  (eq (value-kind v) :text))

(defun value-bin-p (v)
  (eq (value-kind v) :bin))

(defun value-bool-p (v)
  (eq (value-kind v) :bool))

(defun value-size (v)
  (value-count v))

(declaim (inline value-element-vector))
(defun value-element-vector (v)
  "The simple-vector holding V's children in order when V keeps them in one --
a shaped record (in its shape's key order) or a stored list -- else NIL, and
the children are V's alist. Read only: the vector is V's own storage."
  (and (or (value-shape v) (value-is-list v))
       (value-storage v)))

(defun value-has (v key)
  (cond
    ((value-shape v)
     (not (null (gethash key (record-shape-key-map (value-shape v))))))
    ((and (value-is-list v) (value-storage v))
     (let ((idx (parse-list-key key)))
       (and idx (<= 1 idx (length (value-storage v))))))
    (t
     (and (%value-cell v key) t))))

(defun value-get (v key)
  (cond
    ((value-shape v)
     (let ((idx (gethash key (record-shape-key-map (value-shape v)))))
       (when idx
         (svref (value-storage v) idx))))
    ((and (value-is-list v) (value-storage v))
     (let ((idx (parse-list-key key)))
       (when (and idx (<= 1 idx (length (value-storage v))))
         (svref (value-storage v) (1- idx)))))
    (t
     (let ((cell (%value-cell v key)))
       (when cell
         (cdr cell))))))

(defun value-keys (v)
  "V's keys in order, as a fresh list of fresh strings the caller may keep or
change. (%VALUE-KEYS is the library's zero-copy reading, whose strings are
shared with V's shape and with every list.)"
  (mapcar #'copy-seq (%value-keys v)))

(defun %value-keys (v)
  (cond
    ((value-shape v)
     (record-shape-keys (value-shape v)))
    ((and (value-is-list v) (value-storage v))
     (loop for i from 1 to (length (value-storage v))
           collect (format-index-string i)))
    (t
     (mapcar #'car (value-children v)))))

(defun value-values (v)
  (let ((vec (value-element-vector v)))
    (if vec
        (coerce vec 'list)
        (mapcar #'cdr (value-children v)))))

(defun value-entries (v)
  "V's children as a fresh alist of (key . child): the conses and key strings
are the caller's; each child is V's own value, as VALUE-GET returns it."
  (mapcar (lambda (cell) (cons (copy-seq (car cell)) (cdr cell)))
          (%value-entries v)))

(defun %value-entries (v)
  "The zero-copy VALUE-ENTRIES: V's own alist, which the library must not change."
  (ensure-shaped-children v)
  (ensure-list-children v)
  (value-children-internal v))

(defun value-set (v key child)
  "Re-assigning an existing key keeps its original position."
  ;; A key is text too (spec §8).
  (unless (stringp key) (bad-arg "a key must be a string, not ~(~a~)" (type-of key)))
  (when (and (find-if (lambda (c) (> (char-code c) 127)) key) (not (valid-utf8-string-p key)))
    (fail "E_UTF8" "key carries an unpaired surrogate"))
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
           ;; A new key is copied: an SBCL string is mutable, and the caller's
           ;; would rename the key under the value.
           (let* ((key (copy-seq key))
                  (new (list (cons key child))))
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

(declaim (inline scalar-source))
(defun scalar-source (v &optional at)
  "The value that supplies the scalar: V itself, or its first child, recursively."
  (declare (optimize (speed 3) (safety 1)))
  (if (not (eq (value-kind v) :none))
      v
      (let ((cur v)
            (guard 0))
        (declare (type fixnum guard))
        (loop while (eq (value-kind cur) :none)
              do (when (value-null-p cur)
                   (fail "E_NULL" "value is NULL" at))
                 (when (zerop (value-size cur))
                   (fail "E_NO_SCALAR" "value has no scalar and no children" at))
                 (let ((first-val (let ((vec (value-element-vector cur)))
                                    (if vec
                                        (svref vec 0)
                                        (cdr (first (value-children-internal cur)))))))
                   (setf cur first-val))
                 ;; Not reachable from SEL (values are capped at depth 200), but a
                 ;; host can chain VALUE-SETs deeper than that, so the walk stays
                 ;; bounded: a 1,500-deep first-child chain is E_DEPTH here.
                 (incf guard)
                 (when (> guard 1000)
                   (fail "E_DEPTH" "scalar context nested too deeply" at)))
        cur)))

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

(declaim (inline as-dec))
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
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum depth))
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (cond
    ((null (or (value-shape v) (value-children-internal v) (value-is-list v)))
     ;; Fast path for leaf scalar values (numbers, strings, booleans, null)
     (let ((k (value-kind v)))
       (case k
         (:text
          (let ((dec (value-dec-val v)))
            (if dec
                (%make-value-raw :text (value-%scalar v) nil nil 0 nil nil nil nil dec)
                (%make-value-raw :text (value-%scalar v) nil nil 0 nil nil nil nil nil))))
         (:bin
          ;; The octets are shared, as a TEXT scalar's string is: nothing in a program
          ;; writes into a built BIN (every builtin fills a fresh vector), and the
          ;; boundary copies on the way in (MAKE-BIN) and out (TO-NATIVE). Copying
          ;; 500 KB per assignment cost 0.24 ms for nothing.
          (%make-value-raw :bin (value-%scalar v) nil nil 0 nil nil nil nil nil))
         (:bool
          (%make-value-raw :bool (value-%scalar v) nil nil 0 nil nil nil nil nil))
         (otherwise
          (%make-value-raw :none nil nil nil 0 nil nil nil nil nil)))))
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
                   (value-scalar v)
                   (loop for (k . child) in (value-children v)
                         collect (cons k (value-copy-at child (1+ depth) pos)))
                   (value-is-list v))))
       (setf (value-dec-val new-v) (value-dec-val v))
       new-v))))

(defun value-depth-check (v depth pos)
  "The depth walk of VALUE-COPY-AT, without the copy: for a value that is already
private to the caller and need only be checked against the cap (§6.4)."
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum depth))
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (let ((vec (value-element-vector v)))
    (if vec
        (loop for child across (the simple-vector vec)
              do (value-depth-check child (1+ depth) pos))
        (dolist (cell (value-children-internal v))
          (value-depth-check (cdr cell) (1+ depth) pos)))))

;;; --- structural equality (§5.4) --------------------------------------------

(defun value-eql (a b &optional pos)
  "Same kind, equal scalars with numbers *not* normalised, and children with the
same keys in the same order, pairwise EQL."
  (value-eql-at a b 1 pos))

(defun value-eql-at (a b depth pos)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum depth))
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (and (eq (value-kind a) (value-kind b))
       (case (value-kind a)
         (:text
          (let ((da (value-dec-val a))
                (db (value-dec-val b)))
            (if (and da db (null (value-%scalar a)) (null (value-%scalar b)))
                (and (= (dec-digits da) (dec-digits db))
                     (= (dec-scale da) (dec-scale db))
                     (eq (dec-neg da) (dec-neg db)))
                (string= (value-scalar a) (value-scalar b)))))
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
          (let* ((sa (value-storage a))
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
  ;; A value nested past the cap cannot be hashed any more than dumped: answering
  ;; 0 let DEDUPE pass one it could not compare.
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply"))
  (let ((h (sxhash (value-kind v))))
    (case (value-kind v)
      (:text
       ;; Text identity includes spelling and scale, not the decimal cache.
       (setf h (logand most-positive-fixnum
                       (logxor h (sxhash (value-scalar v))))))
      (:bool
       (setf h (logand most-positive-fixnum (logxor h (if (value-scalar v) 12345 67890)))))
      (:bin
       (let ((b (value-scalar v)))
         ;; Every octet goes into the hash. Hashing the length alone put every
         ;; BIN of one size in one bucket, so DEDUPE over N distinct BINs was
         ;; quadratic (8,000 four-byte BINs took 5.8 s). FNV-1a, 32 bit.
         (when (typep b 'vector)
           (let ((x 2166136261))
             (declare (type (unsigned-byte 32) x))
             (loop for o across b
                   do (setf x (logand #xFFFFFFFF (* (logxor x (logand o #xFF)) 16777619))))
             (setf h (logand most-positive-fixnum (logxor h x (sxhash (length b))))))))))
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
                                  (logxor h (sxhash (format-index-string (1+ i))))))
                  (setf h (logand most-positive-fixnum
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

(defun proper-list-p (x)
  "True for a finite NIL-terminated list; a dotted or circular one is refused
rather than left to signal a CL TYPE-ERROR half way through a conversion."
  (handler-case (and (list-length x) t)
    (type-error () nil)))

(defun from-native (x)
  "Convert CL data to a SEL value. Floats are refused outright: they have no
exact decimal form, and SEL has no floating point. Pass a string instead."
  (from-native-at x 1))

(defun from-native-at (x depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  (typecase x
    (null (make-none))
    (value x)
    ((member t) (make-bool t))
    ;; NIL is NULL (and the empty list), so FALSE needs a spelling of its own
    ;; (spec §8).
    ((member :false) (make-bool nil))
    (string (make-text x))
    (integer (make-int x))
    (ratio (bad-arg "~a has no exact decimal form; pass a decimal string instead" x))
    (float (bad-arg "floats have no exact decimal form; pass a decimal string instead"))
    ((vector (unsigned-byte 8)) (make-bin x))
    ;; A plain list is a SEL list, keyed from 1 — so ITEMS[1] means the first
    ;; line on every host. An alist is a keyed value.
    (cons (unless (proper-list-p x)
            (bad-arg "cannot convert a dotted or circular list to SEL"))
          (if (and (consp (first x)) (stringp (car (first x))))
              (let* ((_ (unless (every (lambda (e) (and (consp e) (stringp (car e)))) x)
                          (bad-arg "cannot convert a list mixing keyed pairs with other elements to SEL")))
                     (keys (mapcar (lambda (pair)
                                     (let ((k (car pair)))
                                       (unless (valid-utf8-string-p k)
                                         (fail "E_UTF8" "key carries an unpaired surrogate"))
                                       (copy-seq k)))
                                   x))
                     (n (length keys)))
                (declare (ignore _))
                (if (keys-distinct-p keys)
                    (let* ((shape (get-record-shape keys))
                           (storage (make-array n)))
                      (loop for (nil . val) in x
                            for idx from 0
                            do (setf (svref storage idx) (from-native-at val (1+ depth))))
                      (%make-shaped-value shape storage))
                    (let ((v (make-none)))
                      (loop for k in keys
                            for (nil . val) in x
                            do (value-set v k (from-native-at val (1+ depth))))
                      v)))
              (make-list-value (mapcar (lambda (e) (from-native-at e (1+ depth))) x))))
    (t (bad-arg "cannot convert ~(~a~) to SEL" (type-of x)))))

(defun to-native (v)
  "The inverse of FROM-NATIVE, near enough for reporting: a bare scalar when the
value has no children, otherwise an alist, with the scalar under \"_\"."
  (to-native-at v 1))

(defun to-native-at (v depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  ;; What it returns is the host's own: strings and octet vectors are copies,
  ;; and FALSE is :false, not the NIL that is NULL.
  (let ((scalar (case (value-kind v)
                  ((:text :bin) (copy-seq (value-scalar v)))
                  (:bool (if (value-scalar v) t :false))
                  (t nil))))
    (if (zerop (value-size v))
        scalar
        (let ((entries (cond
                         ((value-shape v)
                          (let* ((shape (value-shape v))
                                 (keys (record-shape-keys shape))
                                 (storage (value-storage v)))
                            ;; Copies: the shape's key strings are shared by every
                            ;; value of that shape.
                            (loop for k in keys
                                  for i from 0
                                  collect (cons (copy-seq k) (to-native-at (svref storage i) (1+ depth))))))
                         ((and (value-is-list v) (value-storage v))
                          (let ((storage (value-storage v))
                                (n (length (value-storage v))))
                            (loop for i from 1 to n
                                  collect (cons (copy-seq (format-index-string i)) (to-native-at (svref storage (1- i)) (1+ depth))))))
                         (t
                          (loop for (k . child) in (value-children v)
                                collect (cons (copy-seq k) (to-native-at child (1+ depth))))))))
          (cond
            ((and (null scalar) (not (eq (value-kind v) :bool))) entries)
            ;; A value's own scalar travels under "_"; with a child of that name
            ;; too, one of them would be lost.
            ((assoc "_" entries :test #'string=)
             (fail "E_BAD_ARG" "a value with both a scalar and a child named \"_\" has no native form"))
            (t (cons (cons "_" scalar) entries)))))))
