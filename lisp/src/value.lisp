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

(defstruct (value (:constructor %make-value (kind scalar children &optional is-list)))
  (kind :none :type keyword)     ; :none :text :bin :bool
  (scalar nil)
  (children nil :type list)      ; list of (key . value), insertion-ordered
  (tail nil :type list)          ; last cons of CHILDREN
  (count 0 :type fixnum)
  (index nil)                    ; key -> cons cell, once COUNT reaches the threshold
  (is-list nil :type boolean))

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
    (dolist (cell (value-children v))
      (setf (gethash (car cell) idx) cell))
    (setf (value-index v) idx)))

(defun %value-cell (v key)
  "The cons cell for KEY, or NIL."
  (let ((idx (value-index v)))
    (if idx
        (gethash key idx)
        (assoc key (value-children v) :test #'string=))))

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
  (%text (etypecase d
           (dec (dec-format d))
           (string (let ((p (dec-parse d)))
                     (unless p (fail "E_NOT_NUM" (format nil "not a number: ~a" d)))
                     (dec-format p))))))

(defun make-int (n) (%text (dec-format (dec-from-int n))))

;;; Builds a list keyed "1".."n". Used by `,` and by list-returning built-ins.
(defun make-list-value (values)
  (let ((v (%make-value :none nil nil t)))
    (loop for x in values
          for i from 1
          do (value-set v (format nil "~d" i) x))
    v))

;;; --- children --------------------------------------------------------------

;;; Kind predicates. The recommended way to branch on kind in every host,
;;; because it is the one spelling that reads the same in all four: the kind
;;; *values* are a keyword here, a string in JS, a class constant in PHP and an
;;; enum in C++, so only a predicate can be documented uniformly. These test the
;;; value's own kind and do not apply scalar context.
(defun value-none-p (v) (eq (value-kind v) :none))

(defun value-null-p (v)
  (and (eq (value-kind v) :none)
       (zerop (value-size v))
       (not (value-is-list v))))

(defun value-vacuous-p (v)
  (cond
    ((value-null-p v) t)
    ((and (eq (value-kind v) :none) (zerop (value-size v))) t)
    ((and (eq (value-kind v) :text) (zerop (value-size v)))
     (let ((s (value-scalar v)))
       (or (zerop (length s))
           (every (lambda (c) (member c '(#\Space #\Tab #\Return #\Newline))) s))))
    (t nil)))

(defun value-text-p (v) (eq (value-kind v) :text))
(defun value-bin-p (v) (eq (value-kind v) :bin))
(defun value-bool-p (v) (eq (value-kind v) :bool))

(defun value-size (v) (value-count v))

(defun value-has (v key) (and (%value-cell v key) t))

(defun value-get (v key) (cdr (%value-cell v key)))

(defun value-keys (v) (mapcar #'car (value-children v)))
(defun value-values (v) (mapcar #'cdr (value-children v)))
(defun value-entries (v) (value-children v))

(defun value-set (v key child)
  "Re-assigning an existing key keeps its original position."
  (let ((cell (%value-cell v key)))
    (if cell
        (setf (cdr cell) child)
        (let ((new (list (cons key child))))
          (if (value-tail v)
              (setf (cdr (value-tail v)) new)
              (setf (value-children v) new))
          (setf (value-tail v) new)
          (incf (value-count v))
          (let ((idx (value-index v)))
            (if idx
                (setf (gethash key idx) (car new))
                (when (>= (value-count v) +index-threshold+) (%build-index v)))))))
  v)

;;; --- scalar context (§3.2) -------------------------------------------------

(defun scalar-source (v &optional at)
  "The value that supplies the scalar: V itself, or its first child, recursively."
  (let ((cur v)
        (guard 0))
    (loop while (eq (value-kind cur) :none)
          do (when (value-null-p cur)
               (fail "E_NULL" "value is NULL" at))
             (when (null (value-children cur))
               (fail "E_NO_SCALAR" "value has no scalar and no children" at))
             (setf cur (cdr (first (value-children cur))))
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
    (or (dec-parse (value-scalar s) at)
        (fail "E_NOT_NUM" (format nil "not a number: ~s" (value-scalar s)) at))))

;;; The non-throwing probe, as ISNUM uses.
(defun looks-numeric (v)
  (if (and (eq (value-kind v) :none) (null (value-children v)))
      nil
      (handler-case
          (let ((s (scalar-source v)))
            (and (eq (value-kind s) :text)
                 (dec-parse (value-scalar s))
                 t))
        (sel-error () nil))))

;;; --- copying ---------------------------------------------------------------

;;; A value's nesting is the third thing spec/SPEC.md §6.4 caps, after the
;;; parser's and the evaluator's, and it was the last one left uncounted.
;;; VALUE-COPY, VALUE-EQL, VALUE-DUMP and the two native conversions each recurse
;;; once per level, so a value nested deeply enough reached the host's own stack:
;;; RecursionError on Python at about a thousand levels, an uncaught RangeError on
;;; JS at about four, a segfault on C++ at about sixty. Three hosts answered where
;;; two died, on the same program.
;;;
;;; The depth rides as a parameter, as it does in COLLECT-DEPS: nothing has to be
;;; released on the way out, so no WITH-DEPTH is needed and all five hosts spell
;;; it the same way. A value of exactly +MAX-DEPTH+ levels is fine; the level past
;;; it is refused. POS is reported when the caller has one -- the evaluator knows
;;; which node asked -- and is NIL for a call from host code, the same convention
;;; as AS-TEXT.
(defun value-copy (v &optional pos)
  "Assignment copies by value: two variables never share structure (§5.7)."
  (value-copy-at v 1 pos))

(defun value-copy-at (v depth pos)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (%value-with-children
   (value-kind v)
   (if (eq (value-kind v) :bin) (copy-seq (value-scalar v)) (value-scalar v))
   (loop for (k . child) in (value-children v)
         collect (cons k (value-copy-at child (1+ depth) pos)))
   (value-is-list v)))

;;; --- structural equality (§5.4) --------------------------------------------

(defun value-eql (a b &optional pos)
  "Same kind, equal scalars with numbers *not* normalised, and children with the
same keys in the same order, pairwise EQL."
  (value-eql-at a b 1 pos))

(defun value-eql-at (a b depth pos)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" pos))
  (and (eq (value-kind a) (value-kind b))
       (case (value-kind a)
         (:text (string= (value-scalar a) (value-scalar b)))
         (:bin (bytes-equal (value-scalar a) (value-scalar b)))
         (:bool (eq (value-scalar a) (value-scalar b)))
         (t t))
       (= (value-size a) (value-size b))
       (loop for (ka . va) in (value-children a)
             for (kb . vb) in (value-children b)
             always (and (string= ka kb)          ; key order is normative
                         (value-eql-at va vb (1+ depth) pos)))))

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
    (if (null (value-children v))
        s
        (concatenate 'string s "{"
                     (format nil "~{~a~^, ~}"
                             (loop for (k . child) in (value-children v)
                                   collect (concatenate 'string (quote-dump k) "="
                                                        (value-dump-at child (1+ depth)))))
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
              (let ((v (make-none)))
                (loop for (k . val) in x
                      do (value-set v k (from-native-at val (1+ depth))))
                v)
              (make-list-value (mapcar (lambda (e) (from-native-at e (1+ depth))) x))))))

(defun to-native (v)
  "The inverse of FROM-NATIVE, near enough for reporting: a bare scalar when the
value has no children, otherwise an alist, with the scalar under \"_\"."
  (to-native-at v 1))

(defun to-native-at (v depth)
  (when (> depth +max-depth+)
    (fail "E_DEPTH" "value nested too deeply" nil))
  (let ((scalar (case (value-kind v)
                  ((:text :bin :bool) (value-scalar v))
                  (t nil))))
    (if (null (value-children v))
        scalar
        (let ((entries (loop for (k . child) in (value-children v)
                             collect (cons k (to-native-at child (1+ depth))))))
          (if (and (null scalar) (not (eq (value-kind v) :bool)))
              entries
              (cons (cons "_" scalar) entries))))))
