;;;; Exact decimal arithmetic on integer magnitudes. See spec/SPEC.md §4.
;;;;
;;;; A decimal is (neg digits scale), meaning (neg ? -1 : 1) * digits / 10^scale.
;;;; `digits` is the unscaled magnitude as a non-negative CL integer (0 for zero).
;;;; Zero is never negative. Scale is part of the value: 2.50 is 250 at scale 2,
;;;; and stays "2.50" through addition. `int-val` caches the signed unscaled
;;;; integer for values within the 60-bit fast path.

(in-package #:sel)

(defconstant +div-scale+ +limit-div-scale+)   ; spec/limits.json

;;; spec/SPEC.md 6.4. These bound the *value*; ROUND's scale cap and POWER's
;;; exponent cap bound *arguments*, and an argument cap is not a value cap --
;;; POWER's base is unbounded, so nesting one POWER inside another multiplies
;;; the exponents and steps straight over the exponent cap. Two independent
;;; numbers rather than one shared budget, because ROUND(99.5, 1000000) is
;;; 1 000 002 digits and legal under the scale cap: a shared budget would have
;;; shrunk what the spec already sanctions.
(defconstant +max-int-digits+ +limit-max-int-digits+)
(defconstant +max-frac-digits+ +limit-max-frac-digits+)
(defconstant +max-int-bits+ 3321929)
(defconstant +fast-scale+ 18)
(defconstant +fast-bits+ 60)

(defstruct (dec (:constructor %make-dec (neg digits scale &optional int-val)))
  (neg nil :type boolean)
  (digits 0 :type integer)
  (scale 0 :type fixnum)
  (int-val nil)
  ;; The canonical text this number was parsed from, kept for a LONG numeral only:
  ;; rendering a million-digit integer is the most expensive thing the core does,
  ;; and a value that was read and is now printed unchanged (`X == Y`, CANON,
  ;; a pass-through) need not be rendered at all. Set once, by DEC-PARSE.
  (text nil))

(defvar *pow10-table*
  (coerce (loop for i from 0 to 18 collect (expt 10 i)) 'simple-vector))

;;; Shared by every thread: a SYNCHRONIZED table (a probe is safe beside an insert)
;;; and one lock around the bounded-generation bookkeeping, whose count/weight/
;;; clear/insert steps are a read-modify-write of four things at once. Without
;;; both, threads doing arithmetic at many scales corrupted the table and lost
;;; the weight.
(defvar *pow10-cache* (make-hash-table :test 'eql :synchronized t))
(defvar *pow10-lock* (sb-thread:make-mutex :name "sel pow10 cache"))
(defconstant +pow10-cache-entries+ 64)
(defconstant +pow10-cache-max-exponent+ 1000000)
(defconstant +pow10-cache-digits+ 1048576)
(defvar *pow10-cache-weight* 0)

;;; Multiplication of big integers. SBCL multiplies bignums limb by limb
;;; (quadratic), which made every operation on a legal million-digit number cost
;;; seconds. Karatsuba above a threshold brings parse, 10^k and the
;;; product check to a fraction of that. Operands are non-negative here; anything
;;; else takes the built-in multiply.
(defconstant +kmul-threshold-bits+ 8192)

(defun kmul (a b)
  (declare (type integer a b))
  (if (or (minusp a) (minusp b))
      (* a b)
      (let ((la (integer-length a))
            (lb (integer-length b)))
        (if (or (< la +kmul-threshold-bits+) (< lb +kmul-threshold-bits+))
            (* a b)
            (let* ((h (ash (max la lb) -1))
                   (mask (1- (ash 1 h)))
                   (a0 (logand a mask)) (a1 (ash a (- h)))
                   (b0 (logand b mask)) (b1 (ash b (- h)))
                   (z0 (kmul a0 b0))
                   (z2 (kmul a1 b1))
                   (z1 (- (kmul (+ a0 a1) (+ b0 b1)) z0 z2)))
              (+ (ash z2 (* 2 h)) (ash z1 h) z0))))))

(defun big-pow10 (k)
  "10^K, through Karatsuba squaring of 5^K then a shift."
  (declare (type (integer 0) k))
  (if (< k 4000)
      (expt 10 k)
      (let ((result 1) (base 5) (e k))
        (loop while (plusp e)
              do (when (oddp e) (setf result (kmul result base)))
                 (setf e (ash e -1))
                 (when (plusp e) (setf base (kmul base base))))
        (ash result k))))

(declaim (inline pow10))
(defun pow10 (k)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum k))
  (if (and (>= k 0) (<= k 18))
      (svref *pow10-table* k)
      (or (gethash k *pow10-cache*)
          (let ((value (big-pow10 k)))
            (when (<= 0 k +pow10-cache-max-exponent+)
              ;; A bounded generation avoids maintaining an LRU list in the
              ;; arithmetic hot path. Oversized powers are never retained.
              (sb-thread:with-mutex (*pow10-lock*)
                (when (or (>= (hash-table-count *pow10-cache*) +pow10-cache-entries+)
                          (> (+ *pow10-cache-weight* k) +pow10-cache-digits+))
                  (clrhash *pow10-cache*)
                  (setf *pow10-cache-weight* 0))
                (setf (gethash k *pow10-cache*) value)
                (incf *pow10-cache-weight* k)))
            value))))

(defun num-digits (n)
  (cond
    ((zerop n) 1)
    ((< (integer-length n) 64)
     (let ((d (1+ (truncate (* (integer-length n) 30103) 100000))))
       (loop while (< n (pow10 (1- d)))
             do (decf d))
       d))
    (t
     ;; One big power, not one per candidate: the bit length gives a count that
     ;; is a LOWER bound (LO, so N >= 10^(LO-1)) within a few dozen of the true
     ;; one, and the rest is found by multiplying that power by ten, which is
     ;; linear, where asking POW10 for each candidate exponent built a new
     ;; million-digit power every time.
     (let* ((lo (1+ (floor (* (1- (integer-length n)) 30102) 100000)))
            (d lo)
            (q (* (pow10 (1- lo)) 10)))
       (loop while (>= n q)
             do (incf d)
                (setf q (* q 10)))
       d))))

(declaim (inline dec-make))
(defun dec-make (neg digits scale &optional int-val)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type integer digits) (type fixnum scale))
  (let* ((actual-neg (if (zerop digits) nil (and neg t))))
    (unless int-val
      (when (and (<= (integer-length digits) +fast-bits+) (<= scale +fast-scale+))
        (setf int-val (if actual-neg (- digits) digits))))
    (%make-dec actual-neg digits scale int-val)))

;;; Refuses a value SEL cannot hold, where it is built rather than where it is
;;; rendered. Every operation that can grow a number passes its result through
;;; here, so DEC-POWER -- repeated squaring over DEC-MUL -- trips on an
;;; intermediate and the enormous value is never allocated.
(defun dec-guard (d at)
  (when (> (dec-scale d) +max-frac-digits+)
    (fail "E_RANGE"
          (format nil "number has more than ~D fractional digits" +max-frac-digits+)
          at))
  ;; Negative when the value is below 1: those render as a single "0".
  (when (and (>= (integer-length (dec-digits d)) +max-int-bits+)
             (> (- (num-digits (dec-digits d)) (dec-scale d)) +max-int-digits+))
    (fail "E_RANGE"
          (format nil "number has more than ~D integer digits" +max-int-digits+)
          at))
  d)

;;; --- construction ----------------------------------------------------------

(defvar *dec-zero* (dec-make nil 0 0 0))

;;; ASCII digits only — never DIGIT-CHAR-P, which on SBCL accepts every Unicode
;;; decimal digit and would make "١" a number. See utf8.lisp.
(defun digits-only-p (s start end)
  (and (< start end)
       (loop for i from start below end always (ascii-digit-p (char s i)))))

(defun dec-number-string-p (text)
  "True when TEXT matches -? digit {digit} [ '.' digit {digit} ] exactly.
No trimming, no sign but a leading minus, no exponent, no leading or trailing dot."
  (let* ((n (length text))
         (i (if (and (plusp n) (char= (char text 0) #\-)) 1 0))
         (dot (position #\. text :start i)))
    (if dot
        (and (digits-only-p text i dot)
             (digits-only-p text (1+ dot) n)
             (not (find #\. text :start (1+ dot))))
        (digits-only-p text i n))))

(defun parse-bignum-string (s start end)
  (let ((len (- end start)))
    (if (<= len 9)
        (parse-integer s :start start :end end)
        (let* ((mid (ash len -1))
               (hi (parse-bignum-string s start (+ start mid)))
               (lo (parse-bignum-string s (+ start mid) end)))
          (+ (kmul hi (pow10 (- len mid))) lo)))))

;;; One pass over an ordinary short numeral (at most 19 characters, so at most 19
;;; digits and an unsigned 64-bit accumulator cannot overflow): accumulate the
;;; digits and check the grammar as it goes, instead of the five scans
;;; (DEC-NUMBER-STRING-P, POSITION, SUBSEQ, CONCATENATE, POSITION-IF) plus a
;;; bignum parse the general path pays (472 ns for "12345.67").
;;; Answers NIL for anything it does not handle -- a longer numeral, anything that
;;; is not exactly -?digits[.digits], a non-simple string -- and the general path
;;; decides. The result is what the general path builds: DEC-MAKE normalises a
;;; zero to no sign, and a short numeral never reaches either cap.
(defun dec-parse-short (text)
  (declare (optimize (speed 3) (safety 0)))
  (typecase text
    ((simple-array character (*))
     (let ((n (length text)))
       (when (and (<= 1 n 19))
         (let* ((neg (char= (schar text 0) #\-))
                (start (if neg 1 0))
                (acc 0)
                (ndigits 0)
                (frac -1))
           (declare (type (unsigned-byte 64) acc) (type fixnum start ndigits frac))
           (when (>= start n) (return-from dec-parse-short nil))
           (loop for i of-type fixnum from start below n
                 do (let ((code (char-code (schar text i))))
                      (cond ((<= 48 code 57)
                             (setf acc (+ (* acc 10) (- code 48)))
                             (incf ndigits)
                             (when (>= frac 0) (incf frac)))
                            ((and (= code 46) (< frac 0) (plusp ndigits))
                             (setf frac 0))
                            (t (return-from dec-parse-short nil)))))
           ;; At least one digit, and a '.' must be followed by one.
           (when (or (zerop ndigits) (eql frac 0)) (return-from dec-parse-short nil))
           (let ((scale (max frac 0)))
             (declare (type fixnum scale))
             (dec-make neg acc scale))))))
    (t nil)))

(defun dec-parse (text &optional at)
  "Return a DEC, or NIL when TEXT is not a number. Callers raise E_NOT_NUM with
the position of the offending node.

A well-formed numeral too big to hold is E_RANGE, not NIL: every character of it
is a digit, so \"not a number\" would be false. Callers that must not signal --
ISNUM's probe -- catch it and answer no."
  (let ((fast (dec-parse-short text)))
    (when fast (return-from dec-parse fast)))
  (when (and (stringp text) (dec-number-string-p text))
    (let* ((len (length text))
           (neg (char= (char text 0) #\-))
           (start (if neg 1 0))
           (dot (position #\. text :start start)))
      (if (and (<= len 18) (null dot))
          (let ((val 0))
            (loop for i from start below len
                  do (setf val (+ (* val 10) (- (char-code (char text i)) 48))))
            (let* ((is-zero (zerop val))
                   (actual-neg (if is-zero nil neg))
                   (int-val (if actual-neg (- val) val)))
              (%make-dec actual-neg val 0 int-val)))
          (let* ((body (if neg (subseq text 1) text))
                 (dot (position #\. body))
                 (int-part (if dot (subseq body 0 dot) body))
                 (frac-part (if dot (subseq body (1+ dot)) ""))
                 (frac-len (length frac-part)))
            (when (> frac-len +max-frac-digits+)
              (fail "E_RANGE"
                    (format nil "number has more than ~D fractional digits" +max-frac-digits+)
                    at))
            (let* ((combined (concatenate 'string int-part frac-part))
                   (first-nz (position-if (lambda (c) (char/= c #\0)) combined))
                   (int-digits (- (if first-nz (- (length combined) first-nz) 1) frac-len)))
              (when (> int-digits +max-int-digits+)
                (fail "E_RANGE"
                      (format nil "number has more than ~D integer digits" +max-int-digits+)
                      at))
              (let* ((digits (if first-nz (parse-bignum-string combined first-nz (length combined)) 0))
                     (d (dec-guard (dec-make neg digits frac-len) at)))
                ;; A long numeral that is already canonical -- no leading zero in
                ;; its integer part, and not a negative zero -- is its own text.
                (when (and (> len 64)
                           (or (char/= (char int-part 0) #\0) (= (length int-part) 1))
                           (not (and neg (zerop digits))))
                  (setf (dec-text d) (copy-seq text)))
                d)))))))

;;; The text of a number whose digits fit an unsigned 62-bit integer and whose scale
;;; is at most 19: the digits are written straight into the result, right to left,
;;; with no intermediate strings (286 ns for 12345.67 through
;;; WRITE-TO-STRING, SUBSEQ and CONCATENATE). Same output as the general path.
(defun dec-format-small (neg digits scale)
  (declare (optimize (speed 3) (safety 0))
           (type (unsigned-byte 62) digits) (type (integer 0 19) scale))
  (let* ((nd (let ((n digits) (k 1))
               (declare (type (unsigned-byte 62) n) (type fixnum k))
               (loop (setf n (floor n 10))
                     (when (zerop n) (return k))
                     (incf k))))
         (int-nd (if (<= nd scale) 1 (- nd scale)))
         (len (+ (if neg 1 0) int-nd (if (plusp scale) (1+ scale) 0)))
         (out (make-string len))
         (pos (1- len))
         (n digits))
    (declare (type fixnum nd int-nd len pos) (type (unsigned-byte 62) n))
    (dotimes (k scale)
      (multiple-value-bind (q r) (floor n 10)
        (setf (schar out pos) (code-char (+ 48 r)) n q)
        (decf pos)))
    (when (plusp scale)
      (setf (schar out pos) #\.)
      (decf pos))
    (if (zerop n)
        (progn (setf (schar out pos) #\0) (decf pos))
        (loop while (plusp n)
              do (multiple-value-bind (q r) (floor n 10)
                   (setf (schar out pos) (code-char (+ 48 r)) n q)
                   (decf pos))))
    (when neg (setf (schar out 0) #\-))
    out))

(defun dec-format (d)
  (let ((digits (dec-digits d)) (scale (dec-scale d)))
    (when (dec-text d) (return-from dec-format (copy-seq (dec-text d))))
    (if (and (typep digits '(unsigned-byte 62)) (typep scale '(integer 0 19)))
        (dec-format-small (dec-neg d) digits scale)
        (dec-format-general d))))

(defun dec-format-general (d)
  (let ((sign (if (dec-neg d) "-" ""))
        (s (write-to-string (dec-digits d) :base 10 :radix nil))
        (scale (dec-scale d)))
    (if (zerop scale)
        (concatenate 'string sign s)
        (let ((padded (if (<= (length s) scale)
                          (concatenate 'string
                                       (make-string (1+ (- scale (length s)))
                                                    :initial-element #\0)
                                       s)
                          s)))
          (concatenate 'string sign
                       (subseq padded 0 (- (length padded) scale))
                       "."
                       (subseq padded (- (length padded) scale)))))))

(defun dec-from-int (n)
  (let* ((abs-n (abs n))
         (neg (minusp n)))
    (if (and (typep n 'fixnum) (<= (integer-length abs-n) +fast-bits+))
        (%make-dec (if (zerop n) nil neg) abs-n 0 n)
        (dec-make neg abs-n 0))))

(declaim (inline dec-zerop))
(defun dec-zerop (d)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec d))
  (zerop (dec-digits d)))

(declaim (inline dec-negate))
(defun dec-negate (d)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec d))
  (let ((iv (dec-int-val d)))
    (if iv
        (let* ((niv (- (the integer iv)))
               (is-zero (zerop niv))
               (neg (if is-zero nil (minusp niv))))
          (%make-dec neg (dec-digits d) (dec-scale d) niv))
        (dec-make (not (dec-neg d)) (dec-digits d) (dec-scale d)))))

;;; The value with the fraction's trailing zeros removed (§7.6 CANON): 1.50 is
;;; 1.5, 2.000 is 2, 100 stays 100, and zero is 0 with no scale and no sign. A
;;; bignum's zeros are counted on its digit string rather than by repeated
;;; division, so a million-digit scale costs one pass.
(defun dec-trim-scale (d)
  (declare (type dec d))
  (let ((digits (dec-digits d))
        (scale (dec-scale d)))
    (cond
      ((zerop digits) (dec-make nil 0 0))
      ((zerop scale) d)
      ((<= (integer-length digits) 62)
       (loop while (and (> scale 0) (zerop (rem digits 10)))
             do (setf digits (truncate digits 10))
                (decf scale))
       (if (= scale (dec-scale d)) d (dec-make (dec-neg d) digits scale)))
      (t
       (let* ((text (write-to-string digits :base 10 :radix nil))
              (len (length text))
              (stop (max 0 (- len scale)))
              (end len))
         (loop while (and (> end stop) (char= (char text (1- end)) #\0))
               do (decf end))
         (if (= end len)
             d
             (dec-make (dec-neg d) (parse-bignum-string text 0 end) (- scale (- len end)))))))))

(declaim (inline dec-abs))
(defun dec-abs (d)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec d))
  (let ((iv (dec-int-val d)))
    (if iv
        (let* ((aiv (abs (the integer iv))))
          (%make-dec nil (dec-digits d) (dec-scale d) aiv))
        (dec-make nil (dec-digits d) (dec-scale d)))))

(defun dec-sign (d) (cond ((dec-zerop d) 0) ((dec-neg d) -1) (t 1)))

;;; True when the value has no fractional part left after its scale is honoured.
(defun dec-integerp (d)
  (or (zerop (dec-scale d))
      (zerop (mod (dec-digits d) (pow10 (dec-scale d))))))

;;; --- arithmetic ------------------------------------------------------------

(defun %dec-add-general (a b &optional at)
  (let ((sa (dec-scale a))
        (sb (dec-scale b)))
    (if (eq (dec-neg a) (dec-neg b))
        (let* ((s (max sa sb))
               (mag-a (* (dec-digits a) (pow10 (- s sa))))
               (mag-b (* (dec-digits b) (pow10 (- s sb))))
               (sum (+ mag-a mag-b)))
          (dec-guard (dec-make (dec-neg a) sum s) at))
        (let* ((s (max sa sb))
               (mag-a (* (dec-digits a) (pow10 (- s sa))))
               (mag-b (* (dec-digits b) (pow10 (- s sb)))))
          (cond
            ((= mag-a mag-b) (dec-make nil 0 s))
            ((> mag-a mag-b) (dec-make (dec-neg a) (- mag-a mag-b) s))
            (t (dec-make (dec-neg b) (- mag-b mag-a) s)))))))

(declaim (inline dec-add))
(defun dec-add (a b &optional at)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec a b))
  (let ((iva (dec-int-val a))
        (ivb (dec-int-val b))
        (sa (dec-scale a))
        (sb (dec-scale b)))
    (declare (type fixnum sa sb))
    (if (and iva ivb (<= sa +fast-scale+) (<= sb +fast-scale+))
        (let ((max-s (max sa sb)))
          (declare (type fixnum max-s))
          (if (and (<= max-s +fast-scale+)
                   (<= (the fixnum (+ (integer-length (the integer iva)) (the fixnum (* (the fixnum (- max-s sa)) 4)))) +fast-bits+)
                   (<= (the fixnum (+ (integer-length (the integer ivb)) (the fixnum (* (the fixnum (- max-s sb)) 4)))) +fast-bits+))
              (let* ((scaled-a (* (the integer iva) (the integer (svref *pow10-table* (- max-s sa)))))
                     (scaled-b (* (the integer ivb) (the integer (svref *pow10-table* (- max-s sb)))))
                     (sum (+ scaled-a scaled-b)))
                (declare (type integer scaled-a scaled-b sum))
                (if (<= (- (ash 1 +fast-bits+)) sum (ash 1 +fast-bits+))
                    (let* ((neg (minusp sum))
                           (abs-sum (abs sum)))
                      (%make-dec (if (zerop sum) nil neg) abs-sum max-s sum))
                    (%dec-add-general a b at)))
              (%dec-add-general a b at)))
        (%dec-add-general a b at))))

(declaim (inline dec-sub))
(defun dec-sub (a b &optional at)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec a b))
  (dec-add a (dec-negate b) at))

(defun %dec-mul-general (a b &optional at)
  (let ((scale (+ (dec-scale a) (dec-scale b)))
        (da (dec-digits a))
        (db (dec-digits b)))
    ;; Refuse what the guard would refuse, BEFORE the multiply: the product has at
    ;; least as many digits as the bit lengths force, and multiplying two
    ;; million-digit operands only to be told so cost tens of seconds.
    ;; The messages and their order are DEC-GUARD's.
    (when (and (plusp da) (plusp db))
      (when (> scale +max-frac-digits+)
        (fail "E_RANGE"
              (format nil "number has more than ~D fractional digits" +max-frac-digits+)
              at))
      (let* ((bits (1- (+ (integer-length da) (integer-length db))))
             (lower (1+ (floor (* (1- bits) 30102) 100000))))
        (when (> (- lower scale) +max-int-digits+)
          (fail "E_RANGE"
                (format nil "number has more than ~D integer digits" +max-int-digits+)
                at))))
    (dec-guard (dec-make (not (eq (dec-neg a) (dec-neg b)))
                         (kmul da db)
                         scale)
               at)))

(declaim (inline dec-mul))
(defun dec-mul (a b &optional at)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec a b))
  (let ((iva (dec-int-val a))
        (ivb (dec-int-val b))
        (sa (dec-scale a))
        (sb (dec-scale b)))
    (declare (type fixnum sa sb))
    (if (and iva ivb)
        (let ((prod-scale (+ sa sb)))
          (declare (type fixnum prod-scale))
          (if (and (<= prod-scale +fast-scale+)
                   (<= (the fixnum (+ (integer-length (the integer iva)) (integer-length (the integer ivb)))) +fast-bits+))
              (let* ((prod (* (the integer iva) (the integer ivb)))
                     (neg (minusp prod))
                     (abs-prod (abs prod)))
                (declare (type integer prod abs-prod))
                (%make-dec (if (zerop prod) nil neg) abs-prod prod-scale prod))
              (%dec-mul-general a b at)))
        (%dec-mul-general a b at))))

(defun %dec-cmp-general (a b)
  (let* ((sa (dec-scale a))
         (sb (dec-scale b))
         (s (max sa sb))
         (mag-a (* (dec-digits a) (pow10 (- s sa))))
         (mag-b (* (dec-digits b) (pow10 (- s sb))))
         (c (cond ((< mag-a mag-b) -1) ((> mag-a mag-b) 1) (t 0))))
    (if (dec-neg a) (- c) c)))

(declaim (inline dec-cmp))
(defun dec-cmp (a b)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec a b))
  (cond
    ((and (dec-zerop a) (dec-zerop b)) 0)
    ((not (eq (dec-neg a) (dec-neg b))) (if (dec-neg a) -1 1))
    ((and (dec-int-val a) (dec-int-val b)
          (<= (dec-scale a) +fast-scale+) (<= (dec-scale b) +fast-scale+))
     (let ((sa (dec-scale a))
           (sb (dec-scale b))
           (va (the integer (dec-int-val a)))
           (vb (the integer (dec-int-val b))))
       (declare (type fixnum sa sb))
       (if (= sa sb)
           (cond ((< va vb) -1) ((> va vb) 1) (t 0))
           (let* ((max-s (max sa sb))
                  (diff-a (- max-s sa))
                  (diff-b (- max-s sb)))
             (declare (type fixnum max-s diff-a diff-b))
             (if (and (<= max-s +fast-scale+)
                      (<= (the fixnum (+ (integer-length va) (the fixnum (* diff-a 4)))) +fast-bits+)
                      (<= (the fixnum (+ (integer-length vb) (the fixnum (* diff-b 4)))) +fast-bits+))
                 (let ((scaled-a (* va (the integer (svref *pow10-table* diff-a))))
                       (scaled-b (* vb (the integer (svref *pow10-table* diff-b)))))
                   (declare (type integer scaled-a scaled-b))
                   (cond ((< scaled-a scaled-b) -1) ((> scaled-a scaled-b) 1) (t 0)))
                 (%dec-cmp-general a b))))))
    (t (%dec-cmp-general a b))))

;;; Exact when the quotient terminates within +div-scale+ fractional digits (and
;;; then reported at its minimal scale); otherwise rounded half away from zero to
;;; exactly +div-scale+ digits. So 4/2 is "2" and 1/3 is "0.3333333333".
(defun dec-div (a b &optional at)
  (when (dec-zerop b) (fail "E_DIV_ZERO" "division by zero" at))
  (let* ((n (* (dec-digits a) (pow10 (+ (dec-scale b) +div-scale+))))
         (d (* (dec-digits b) (pow10 (dec-scale a))))
         (neg (not (eq (dec-neg a) (dec-neg b)))))
    (multiple-value-bind (q r)
        ;; Two word-sized operands divide with the machine's divide: the generic
        ;; TRUNCATE was two thirds of the time of an ordinary division.
        (if (and (typep n '(unsigned-byte 62)) (typep d '(unsigned-byte 62)))
            (truncate (the (unsigned-byte 62) n) (the (unsigned-byte 62) d))
            (truncate n d))
      (if (zerop r)
          ;; Exact: drop trailing zeros to reach the minimal scale.
          (let ((digits q)
                (scale +div-scale+))
            (if (typep digits '(unsigned-byte 62))
                ;; A word-sized quotient strips its zeros with word arithmetic
                ;; (division by a constant is a multiply): the generic MOD and
                ;; TRUNCATE here were most of an exact division.
                (let ((word digits))
                  (declare (type (unsigned-byte 62) word) (type fixnum scale))
                  (loop while (and (plusp scale) (zerop (mod word 10)))
                        do (setf word (truncate word 10))
                           (decf scale))
                  (setf digits word))
                (loop while (and (plusp scale)
                                 (zerop (mod digits 10)))
                      do (setf digits (truncate digits 10))
                         (decf scale)))
            (when (zerop digits) (setf scale 0))
            (dec-guard (dec-make neg digits scale) at))
          ;; Inexact: round half away from zero.
          (let ((final-q (if (>= (* 2 r) d) (1+ q) q)))
            (dec-guard (dec-make neg final-q +div-scale+) at))))))

;;; Remainder of truncated division: takes the sign of the dividend.
(defun dec-mod (a b &optional at)
  (when (dec-zerop b) (fail "E_DIV_ZERO" "modulo by zero" at))
  (let* ((s (max (dec-scale a) (dec-scale b)))
         (mag-a (* (dec-digits a) (pow10 (- s (dec-scale a)))))
         (mag-b (* (dec-digits b) (pow10 (- s (dec-scale b)))))
         (rem (mod mag-a mag-b)))
    (dec-make (dec-neg a) rem s)))

;;; --- rounding. Every rounding in SEL is half away from zero (§4.4). ---------

(defun dec-round (d n &optional at)
  (if (>= n (dec-scale d))
      (dec-guard (dec-make (dec-neg d) (* (dec-digits d) (pow10 (- n (dec-scale d)))) n) at)
      (let ((p (pow10 (- (dec-scale d) n))))
        (multiple-value-bind (q r) (truncate (dec-digits d) p)
          (let ((final-q (if (>= (* 2 r) p) (1+ q) q)))
            (dec-guard (dec-make (dec-neg d) final-q n) at))))))

(defun dec-trunc (d)
  (if (zerop (dec-scale d))
      d
      (dec-make (dec-neg d) (truncate (dec-digits d) (pow10 (dec-scale d))) 0)))

;;; FLOOR and CEIL can carry: a maximum-size 999...9.5 rounds up to a number one
;;; digit past the cap, so the result goes through DEC-GUARD at the call.
(defun dec-floor (d &optional at)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (truncate (dec-digits d) (pow10 (dec-scale d)))
        (dec-guard (dec-make (dec-neg d)
                             (if (and (dec-neg d) (not (zerop r))) (1+ q) q)
                             0)
                   at))))

(defun dec-ceil (d &optional at)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (truncate (dec-digits d) (pow10 (dec-scale d)))
        (dec-guard (dec-make (dec-neg d)
                             (if (and (not (dec-neg d)) (not (zerop r))) (1+ q) q)
                             0)
                   at))))

;;; N must be a non-negative integer; the result scale is scale(x) * n, which
;;; falls out of repeated multiplication.
(defun dec-power (a n &optional at)
  (let ((result (dec-make nil 1 0))
        (base a)
        (e n))
    (loop while (plusp e)
          do (when (oddp e) (setf result (dec-mul result base at)))
             (setf e (ash e -1))
             (when (plusp e) (setf base (dec-mul base base at))))
    result))

(declaim (inline dec-to-int))
(defun dec-to-int (d)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type dec d))
  (let ((iv (dec-int-val d)))
    (if iv
        (if (zerop (dec-scale d))
            (the integer iv)
            (truncate (the integer iv) (the integer (svref *pow10-table* (dec-scale d)))))
        (let ((tr (dec-trunc d)))
          (if (dec-neg tr) (- (dec-digits tr)) (dec-digits tr))))))
