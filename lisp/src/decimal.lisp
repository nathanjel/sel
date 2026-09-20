;;;; Exact decimal arithmetic on integer magnitudes. See spec/SPEC.md §4.
;;;;
;;;; A decimal is (neg digits scale), meaning (neg ? -1 : 1) * digits / 10^scale.
;;;; `digits` is the unscaled magnitude as a non-negative CL integer (0 for zero).
;;;; Zero is never negative. Scale is part of the value: 2.50 is 250 at scale 2,
;;;; and stays "2.50" through addition. `int-val` caches the signed unscaled
;;;; integer for values within the 60-bit fast path.

(in-package #:sel)

(defconstant +div-scale+ 10)

;;; spec/SPEC.md 6.4. These bound the *value*; ROUND's scale cap and POWER's
;;; exponent cap bound *arguments*, and an argument cap is not a value cap --
;;; POWER's base is unbounded, so nesting one POWER inside another multiplies
;;; the exponents and steps straight over the exponent cap. Two independent
;;; numbers rather than one shared budget, because ROUND(99.5, 1000000) is
;;; 1 000 002 digits and legal under the scale cap: a shared budget would have
;;; shrunk what the spec already sanctions.
(defconstant +max-int-digits+ 1000000)
(defconstant +max-frac-digits+ 1000000)
(defconstant +max-int-bits+ 3321929)
(defconstant +fast-scale+ 18)
(defconstant +fast-bits+ 60)

(defstruct (dec (:constructor %make-dec (neg digits scale &optional int-val)))
  (neg nil :type boolean)
  (digits 0 :type integer)
  (scale 0 :type fixnum)
  (int-val nil))

(defvar *pow10-table*
  (coerce (loop for i from 0 to 18 collect (expt 10 i)) 'simple-vector))

(defvar *pow10-cache* (make-hash-table :test 'eql))
(defconstant +pow10-cache-entries+ 64)
(defconstant +pow10-cache-max-exponent+ 1000000)
(defconstant +pow10-cache-digits+ 1048576)
(defvar *pow10-cache-weight* 0)

(declaim (inline pow10))
(defun pow10 (k)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type fixnum k))
  (if (and (>= k 0) (<= k 18))
      (svref *pow10-table* k)
      (or (gethash k *pow10-cache*)
          (let ((value (expt 10 k)))
            (when (<= 0 k +pow10-cache-max-exponent+)
              ;; A bounded generation avoids maintaining an LRU list in the
              ;; arithmetic hot path. Oversized powers are never retained.
              (when (or (>= (hash-table-count *pow10-cache*) +pow10-cache-entries+)
                        (> (+ *pow10-cache-weight* k) +pow10-cache-digits+))
                (clrhash *pow10-cache*)
                (setf *pow10-cache-weight* 0))
              (setf (gethash k *pow10-cache*) value)
              (incf *pow10-cache-weight* k))
            value))))

(defun num-digits (n)
  (if (zerop n)
      1
      (let ((d (1+ (truncate (* (integer-length n) 30103) 100000))))
        (loop while (< n (pow10 (1- d)))
              do (decf d))
        d)))

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
          (+ (* hi (pow10 (- len mid))) lo)))))

(defun dec-parse (text &optional at)
  "Return a DEC, or NIL when TEXT is not a number. Callers raise E_NOT_NUM with
the position of the offending node.

A well-formed numeral too big to hold is E_RANGE, not NIL: every character of it
is a digit, so \"not a number\" would be false. Callers that must not signal --
ISNUM's probe -- catch it and answer no."
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
              (let ((digits (if first-nz (parse-bignum-string combined first-nz (length combined)) 0)))
                (dec-guard (dec-make neg digits frac-len) at))))))))

(defun dec-format (d)
  (let ((sign (if (dec-neg d) "-" ""))
        (s (write-to-string (dec-digits d) :base 10))
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
  (dec-guard (dec-make (not (eq (dec-neg a) (dec-neg b)))
                       (* (dec-digits a) (dec-digits b))
                       (+ (dec-scale a) (dec-scale b)))
             at))

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
    (multiple-value-bind (q r) (truncate n d)
      (if (zerop r)
          ;; Exact: drop trailing zeros to reach the minimal scale.
          (let ((digits q)
                (scale +div-scale+))
            (loop while (and (plusp scale)
                             (zerop (mod digits 10)))
                  do (setf digits (truncate digits 10))
                     (decf scale))
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

(defun dec-floor (d)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (truncate (dec-digits d) (pow10 (dec-scale d)))
        (dec-make (dec-neg d)
                  (if (and (dec-neg d) (not (zerop r))) (1+ q) q)
                  0))))

(defun dec-ceil (d)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (truncate (dec-digits d) (pow10 (dec-scale d)))
        (dec-make (dec-neg d)
                  (if (and (not (dec-neg d)) (not (zerop r))) (1+ q) q)
                  0))))

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
