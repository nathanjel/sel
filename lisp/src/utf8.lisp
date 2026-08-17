;;;; UTF-8 codec, hand-written on purpose.
;;;;
;;;; The host's own facilities are not used: SBCL's decoders are lenient where
;;;; the spec demands E_UTF8, and its string comparison is code-point order where
;;;; SEL's `$` family is defined as bytewise. Every length, offset and slice in
;;;; SEL counts code points, and that has to be true in every host or nothing
;;;; else is.
;;;;
;;;; A CL character is a code point, so a CL string is already the code point
;;;; sequence SEL wants — no separate code point layer is needed here, unlike in
;;;; the JS host. What is needed is a strict codec at the TEXT/BIN boundary.

(in-package #:sel)

(deftype octet () '(unsigned-byte 8))
(deftype octets () '(vector (unsigned-byte 8)))

(defun make-octets (&optional (size 0))
  (make-array size :element-type 'octet :adjustable t :fill-pointer size))

(defun octets-from-list (list)
  (make-array (length list) :element-type 'octet :initial-contents list))

;;; A string carrying a lone surrogate has no UTF-8 encoding, so it cannot be a
;;; SEL TEXT value. SBCL will happily hold one, so it has to be checked.
(defun encode-utf8 (string &optional at)
  (let ((out (make-array (length string) :element-type 'octet
                                         :adjustable t :fill-pointer 0)))
    (loop for ch across string
          for c = (char-code ch)
          do (cond
               ((<= #xd800 c #xdfff)
                (fail "E_UTF8" "unpaired surrogate" at))
               ((< c #x80)
                (vector-push-extend c out))
               ((< c #x800)
                (vector-push-extend (logior #xc0 (ash c -6)) out)
                (vector-push-extend (logior #x80 (logand c #x3f)) out))
               ((< c #x10000)
                (vector-push-extend (logior #xe0 (ash c -12)) out)
                (vector-push-extend (logior #x80 (logand (ash c -6) #x3f)) out)
                (vector-push-extend (logior #x80 (logand c #x3f)) out))
               (t
                (vector-push-extend (logior #xf0 (ash c -18)) out)
                (vector-push-extend (logior #x80 (logand (ash c -12) #x3f)) out)
                (vector-push-extend (logior #x80 (logand (ash c -6) #x3f)) out)
                (vector-push-extend (logior #x80 (logand c #x3f)) out))))
    (coerce out '(simple-array octet (*)))))

(defun decode-utf8 (bytes &optional at)
  "Decode BYTES as UTF-8, or raise E_UTF8. Strict: rejects overlong forms,
surrogates, values above U+10FFFF and truncated sequences. No replacement
characters, ever."
  (let ((out (make-string-output-stream))
        (n (length bytes))
        (i 0))
    (loop while (< i n) do
      (let ((b (aref bytes i)))
        (if (< b #x80)
            (progn (write-char (code-char b) out) (incf i))
            (let (need cp lo hi)
              (cond
                ((<= #xc2 b #xdf) (setf need 1 cp (logand b #x1f) lo #x80 hi #xbf))
                ((= b #xe0) (setf need 2 cp 0 lo #xa0 hi #xbf))
                ((<= #xe1 b #xec) (setf need 2 cp (logand b #x0f) lo #x80 hi #xbf))
                ((= b #xed) (setf need 2 cp #x0d lo #x80 hi #x9f))
                ((<= #xee b #xef) (setf need 2 cp (logand b #x0f) lo #x80 hi #xbf))
                ((= b #xf0) (setf need 3 cp 0 lo #x90 hi #xbf))
                ((<= #xf1 b #xf3) (setf need 3 cp (logand b #x07) lo #x80 hi #xbf))
                ((= b #xf4) (setf need 3 cp 4 lo #x80 hi #x8f))
                (t (fail "E_UTF8"
                         (format nil "invalid start byte 0x~(~2,'0x~) at byte ~d" b i)
                         at)))
              (when (>= (+ i need) n)
                (fail "E_UTF8" (format nil "truncated sequence at byte ~d" i) at))
              (loop for k from 1 to need
                    for c = (aref bytes (+ i k))
                    for min = (if (= k 1) lo #x80)
                    for max = (if (= k 1) hi #xbf)
                    do (when (or (< c min) (> c max))
                         (fail "E_UTF8"
                               (format nil "invalid continuation byte at byte ~d" (+ i k))
                               at))
                       (setf cp (logior (ash cp 6) (logand c #x3f))))
              (write-char (code-char cp) out)
              (incf i (1+ need))))))
    (get-output-stream-string out)))

(defun bytes-to-hex (bytes)
  (string-downcase (with-output-to-string (s)
                     (loop for b across bytes do (format s "~2,'0x" b)))))

(defun bytes-equal (a b)
  (and (= (length a) (length b))
       (every #'= a b)))

;;; Bytewise, as spec/SPEC.md §5.3 requires. CL's STRING< is code-point order,
;;; which disagrees with byte order — never use it for the `$` family.
(defun bytes-compare (a b)
  (let ((n (min (length a) (length b))))
    (loop for i from 0 below n
          for x = (aref a i)
          for y = (aref b i)
          do (when (/= x y) (return-from bytes-compare (if (< x y) -1 1))))
    (cond ((= (length a) (length b)) 0)
          ((< (length a) (length b)) -1)
          (t 1))))

(defun valid-utf8-string-p (string)
  "True when STRING can be encoded as UTF-8, i.e. carries no lone surrogate."
  (loop for ch across string
        never (<= #xd800 (char-code ch) #xdfff)))

;;; --- ASCII digits ----------------------------------------------------------
;;;
;;; CL's DIGIT-CHAR-P is a Unicode predicate on SBCL: it accepts every character
;;; with a decimal digit value, so U+0661 ARABIC-INDIC DIGIT ONE is a digit and
;;; " ١" would parse as a number. Nothing in SEL is ever Unicode-digit-aware —
;;; number literals, \u{...} escapes, hex, regex quantifiers and $1 replacement
;;; references are all ASCII by specification. Use these, never DIGIT-CHAR-P.
;;;
;;; This is the same trap as \d matching Arabic-Indic digits under PCRE's UCP,
;;; which is why spec/SPEC.md §7.8 expands \d to [0-9]. The differential fuzzer
;;; found it here too.

(declaim (inline ascii-digit-p ascii-digit-value ascii-hex-value))

(defun ascii-digit-p (c) (char<= #\0 c #\9))

(defun ascii-digit-value (c)
  (when (char<= #\0 c #\9) (- (char-code c) 48)))

(defun ascii-hex-value (c)
  (cond ((char<= #\0 c #\9) (- (char-code c) 48))
        ((char<= #\a c #\f) (+ 10 (- (char-code c) (char-code #\a))))
        ((char<= #\A c #\F) (+ 10 (- (char-code c) (char-code #\A))))
        (t nil)))
