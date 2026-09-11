;;;; Exact decimal arithmetic on digit strings. See spec/SPEC.md §4.
;;;;
;;;; Ported line for line from js/src/decimal.mjs and php/src/Dec.php; the four
;;;; must stay recognisably the same code, because tools/check-decimal.sh is the
;;;; only thing standing between a subtle rounding difference and a wrong invoice.
;;;;
;;;; CL has bignums and rationals, and neither is used here. A rational cannot
;;;; represent SEL's scale — 2.50 and 2.5 are the same rational and different SEL
;;;; values — and division has a specific minimal-scale-when-exact rule (§4.3)
;;;; that no rational library reproduces. Digit strings it is, exactly as
;;;; everywhere else.
;;;;
;;;; A decimal is (neg digits scale), meaning (neg ? -1 : 1) * digits / 10^scale.
;;;; `digits` is the unscaled integer as a string with no leading zeros ("0" for
;;;; zero). Zero is never negative. Scale is part of the value: 2.50 is "250" at
;;;; scale 2, and stays "2.50" through addition.

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

(defstruct (dec (:constructor %make-dec (neg digits scale &optional int-val)))
  (neg nil :type boolean)
  (digits "0" :type simple-string)
  (scale 0 :type fixnum)
  (int-val nil))

(defvar *pow10-table*
  (coerce (loop for i from 0 to 18 collect (expt 10 i)) 'simple-vector))

(defun dec-make (neg digits scale &optional int-val)
  (let* ((d (coerce digits 'simple-string))
         (is-zero (string= d "0"))
         (actual-neg (if is-zero nil (and neg t))))
    (unless int-val
      (when (and (<= (length d) 18) (<= scale 18))
        (let ((iv (parse-integer d)))
          (setf int-val (if actual-neg (- iv) iv)))))
    (%make-dec actual-neg d scale int-val)))

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
  (when (> (- (length (dec-digits d)) (dec-scale d)) +max-int-digits+)
    (fail "E_RANGE"
          (format nil "number has more than ~D integer digits" +max-int-digits+)
          at))
  d)

;;; --- digit-string primitives (non-negative, no leading zeros) ---------------

(defun dstrip (s)
  (let ((i 0)
        (n (length s)))
    (loop while (and (< i (1- n)) (char= (char s i) #\0)) do (incf i))
    (if (zerop i) s (subseq s i))))

(defun cmp-abs (a b)
  (cond ((/= (length a) (length b)) (if (< (length a) (length b)) -1 1))
        ((string= a b) 0)
        ((string< a b) -1)
        (t 1)))

(defun add-abs (a b)
  (let ((out (make-array 0 :element-type 'character :adjustable t :fill-pointer 0))
        (i (1- (length a)))
        (j (1- (length b)))
        (carry 0))
    (loop while (or (>= i 0) (>= j 0) (plusp carry))
          do (let ((s (+ (if (>= i 0) (- (char-code (char a i)) 48) 0)
                         (if (>= j 0) (- (char-code (char b j)) 48) 0)
                         carry)))
               (decf i)
               (decf j)
               (vector-push-extend (code-char (+ 48 (mod s 10))) out)
               (setf carry (if (>= s 10) 1 0))))
    (coerce (nreverse out) 'simple-string)))

;;; Requires a >= b.
(defun sub-abs (a b)
  (let ((out (make-array 0 :element-type 'character :adjustable t :fill-pointer 0))
        (i (1- (length a)))
        (j (1- (length b)))
        (borrow 0))
    (loop while (>= i 0)
          do (let ((s (- (- (char-code (char a i)) 48)
                         (if (>= j 0) (- (char-code (char b j)) 48) 0)
                         borrow)))
               (decf i)
               (decf j)
               (if (minusp s)
                   (progn (incf s 10) (setf borrow 1))
                   (setf borrow 0))
               (vector-push-extend (code-char (+ 48 s)) out)))
    (dstrip (coerce (nreverse out) 'simple-string))))

(defun mul-abs (a b)
  (if (or (string= a "0") (string= b "0"))
      "0"
      (let* ((n (length a))
             (m (length b))
             (acc (make-array (+ n m) :initial-element 0)))
        (loop for i from (1- n) downto 0
              for av = (- (char-code (char a i)) 48)
              unless (zerop av)
                do (let ((carry 0))
                     (loop for j from (1- m) downto 0
                           do (let ((tt (+ (aref acc (+ i j 1))
                                           (* av (- (char-code (char b j)) 48))
                                           carry)))
                                (setf (aref acc (+ i j 1)) (mod tt 10))
                                (setf carry (floor tt 10))))
                     (incf (aref acc i) carry)))
        (dstrip (map 'simple-string (lambda (d) (code-char (+ 48 d))) acc)))))

;;; Schoolbook long division. Trial digits by repeated subtraction — at most nine
;;; per output digit, which keeps it obviously correct and trivial to port.
(defun divmod-abs (a b)
  "Return (values quotient remainder), or NIL when B is zero."
  (cond
    ((string= b "0") nil)
    ((minusp (cmp-abs a b)) (values "0" a))
    (t
     (let ((q (make-array 0 :element-type 'character :adjustable t :fill-pointer 0))
           (r "0"))
       (loop for i from 0 below (length a)
             do (setf r (dstrip (concatenate 'string r (string (char a i)))))
                (let ((k 0))
                  (loop while (>= (cmp-abs r b) 0)
                        do (setf r (sub-abs r b))
                           (incf k))
                  (vector-push-extend (code-char (+ 48 k)) q)))
       (values (dstrip (coerce q 'simple-string)) r)))))

(defun scale-up (digits k)
  (cond ((<= k 0) digits)
        ((string= digits "0") "0")
        (t (concatenate 'string digits (make-string k :initial-element #\0)))))

(defun pow10 (k)
  (if (zerop k) "1" (concatenate 'string "1" (make-string k :initial-element #\0))))

;;; --- construction ----------------------------------------------------------

(defvar *dec-zero* (dec-make nil "0" 0))

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

(defun dec-parse (text &optional at)
  "Return a DEC, or NIL when TEXT is not a number. Callers raise E_NOT_NUM with
the position of the offending node.

A well-formed numeral too big to hold is E_RANGE, not NIL: every character of it
is a digit, so \"not a number\" would be false. Callers that must not signal --
ISNUM's probe -- catch it and answer no."
  (when (and (stringp text) (dec-number-string-p text))
    (let ((len (length text)))
      (if (<= len 18)
          (let* ((neg (char= (char text 0) #\-))
                 (start (if neg 1 0))
                 (dot (position #\. text :start start))
                 (frac-len (if dot (- len 1 dot) 0))
                 (val 0)
                 (has-nonzero nil))
            (loop for i from start below len
                  unless (= i (or dot -1))
                    do (let ((digit (- (char-code (char text i)) 48)))
                         (when (plusp digit) (setf has-nonzero t))
                         (setf val (+ (* val 10) digit))))
            (let* ((is-zero (not has-nonzero))
                   (actual-neg (if is-zero nil neg))
                   (int-val (if actual-neg (- val) val))
                   (digits (if is-zero "0" (format nil "~d" val))))
              (dec-guard (%make-dec actual-neg digits frac-len int-val) at)))
          (let* ((neg (char= (char text 0) #\-))
                 (body (if neg (subseq text 1) text))
                 (dot (position #\. body))
                 (int-part (if dot (subseq body 0 dot) body))
                 (frac-part (if dot (subseq body (1+ dot)) "")))
            (dec-guard (dec-make neg (dstrip (concatenate 'string int-part frac-part))
                                 (length frac-part))
                       at))))))

(defun dec-format (d)
  (let ((sign (if (dec-neg d) "-" ""))
        (digits (dec-digits d))
        (scale (dec-scale d)))
    (if (zerop scale)
        (concatenate 'string sign digits)
        (let ((padded (if (<= (length digits) scale)
                          (concatenate 'string
                                       (make-string (1+ (- scale (length digits)))
                                                    :initial-element #\0)
                                       digits)
                          digits)))
          (concatenate 'string sign
                       (subseq padded 0 (- (length padded) scale))
                       "."
                       (subseq padded (- (length padded) scale)))))))

(defun dec-from-int (n)
  (let* ((abs-n (abs n))
         (neg (minusp n))
         (s (format nil "~d" abs-n)))
    (if (and (typep n 'fixnum) (<= (length s) 18))
        (%make-dec (if (zerop n) nil neg) s 0 n)
        (dec-make neg s 0 n))))

(defun dec-zerop (d) (string= (dec-digits d) "0"))
(defun dec-negate (d)
  (if (dec-int-val d)
      (let* ((iv (- (dec-int-val d)))
             (is-zero (zerop iv))
             (neg (if is-zero nil (minusp iv))))
        (%make-dec neg (dec-digits d) (dec-scale d) iv))
      (dec-make (not (dec-neg d)) (dec-digits d) (dec-scale d))))
(defun dec-abs (d) (dec-make nil (dec-digits d) (dec-scale d)))
(defun dec-sign (d) (cond ((dec-zerop d) 0) ((dec-neg d) -1) (t 1)))

;;; True when the value has no fractional part left after its scale is honoured.
(defun dec-integerp (d)
  (or (zerop (dec-scale d))
      (multiple-value-bind (q r) (divmod-abs (dec-digits d) (pow10 (dec-scale d)))
        (declare (ignore q))
        (string= r "0"))))

;;; --- arithmetic ------------------------------------------------------------

(defun dec-aligned (a b)
  (let ((s (max (dec-scale a) (dec-scale b))))
    (values (scale-up (dec-digits a) (- s (dec-scale a)))
            (scale-up (dec-digits b) (- s (dec-scale b)))
            s)))

(defun %dec-add-general (a b &optional at)
  (multiple-value-bind (aa bb s) (dec-aligned a b)
    ;; Only true addition can grow: a difference is never wider than its
    ;; operands, and the aligned scale is the larger of two already legal ones.
    (if (eq (dec-neg a) (dec-neg b))
        (dec-guard (dec-make (dec-neg a) (add-abs aa bb) s) at)
        (let ((c (cmp-abs aa bb)))
          (cond ((zerop c) (dec-make nil "0" s))
                ((plusp c) (dec-make (dec-neg a) (sub-abs aa bb) s))
                (t (dec-make (dec-neg b) (sub-abs bb aa) s)))))))

(defun dec-add (a b &optional at)
  (let ((iva (dec-int-val a))
        (ivb (dec-int-val b))
        (sa (dec-scale a))
        (sb (dec-scale b)))
    (if (and iva ivb (<= sa 18) (<= sb 18))
        (let ((max-s (max sa sb)))
          (if (and (<= max-s 18)
                   (<= (+ (integer-length iva) (* (- max-s sa) 4)) 60)
                   (<= (+ (integer-length ivb) (* (- max-s sb) 4)) 60))
              (let* ((scaled-a (* iva (svref *pow10-table* (- max-s sa))))
                     (scaled-b (* ivb (svref *pow10-table* (- max-s sb))))
                     (sum (+ scaled-a scaled-b)))
                (if (<= -4611686018427387900 sum 4611686018427387900)
                    (let* ((neg (minusp sum))
                           (abs-sum (abs sum))
                           (d-str (if (zerop sum) "0" (format nil "~d" abs-sum))))
                      (%make-dec (if (zerop sum) nil neg) d-str max-s sum))
                    (%dec-add-general a b at)))
              (%dec-add-general a b at)))
        (%dec-add-general a b at))))

(defun dec-sub (a b &optional at) (dec-add a (dec-negate b) at))

(defun %dec-mul-general (a b &optional at)
  (dec-guard (dec-make (not (eq (dec-neg a) (dec-neg b)))
                       (mul-abs (dec-digits a) (dec-digits b))
                       (+ (dec-scale a) (dec-scale b)))
             at))

(defun dec-mul (a b &optional at)
  (let ((iva (dec-int-val a))
        (ivb (dec-int-val b))
        (sa (dec-scale a))
        (sb (dec-scale b)))
    (if (and iva ivb)
        (let ((prod-scale (+ sa sb)))
          (if (and (<= prod-scale 18)
                   (<= (+ (integer-length iva) (integer-length ivb)) 60))
              (let* ((prod (* iva ivb))
                     (neg (minusp prod))
                     (abs-prod (abs prod))
                     (d-str (if (zerop prod) "0" (format nil "~d" abs-prod))))
                (%make-dec (if (zerop prod) nil neg) d-str prod-scale prod))
              (%dec-mul-general a b at)))
        (%dec-mul-general a b at))))

(defun %dec-cmp-general (a b)
  (multiple-value-bind (aa bb s) (dec-aligned a b)
    (declare (ignore s))
    (let ((c (cmp-abs aa bb)))
      (if (dec-neg a) (- c) c))))

(defun dec-cmp (a b)
  (cond
    ((and (dec-zerop a) (dec-zerop b)) 0)
    ((not (eq (dec-neg a) (dec-neg b))) (if (dec-neg a) -1 1))
    ((and (dec-int-val a) (dec-int-val b)
          (<= (dec-scale a) 18) (<= (dec-scale b) 18))
     (let ((sa (dec-scale a))
           (sb (dec-scale b))
           (va (dec-int-val a))
           (vb (dec-int-val b)))
       (if (= sa sb)
           (cond ((< va vb) -1) ((> va vb) 1) (t 0))
           (let* ((max-s (max sa sb))
                  (diff-a (- max-s sa))
                  (diff-b (- max-s sb)))
             (if (and (<= max-s 18)
                      (<= (+ (integer-length va) (* diff-a 4)) 60)
                      (<= (+ (integer-length vb) (* diff-b 4)) 60))
                 (let ((scaled-a (* va (svref *pow10-table* diff-a)))
                       (scaled-b (* vb (svref *pow10-table* diff-b))))
                   (cond ((< scaled-a scaled-b) -1) ((> scaled-a scaled-b) 1) (t 0)))
                 (%dec-cmp-general a b))))))
    (t (%dec-cmp-general a b))))

;;; Exact when the quotient terminates within +div-scale+ fractional digits (and
;;; then reported at its minimal scale); otherwise rounded half away from zero to
;;; exactly +div-scale+ digits. So 4/2 is "2" and 1/3 is "0.3333333333".
(defun dec-div (a b &optional at)
  (when (dec-zerop b) (fail "E_DIV_ZERO" "division by zero" at))
  (let ((n (scale-up (dec-digits a) (dec-scale b)))
        (d (scale-up (dec-digits b) (dec-scale a)))
        (neg (not (eq (dec-neg a) (dec-neg b)))))
    (multiple-value-bind (q r) (divmod-abs (scale-up n +div-scale+) d)
      (if (string= r "0")
          ;; Exact: drop trailing zeros to reach the minimal scale.
          (let ((digits q)
                (scale +div-scale+))
            (loop while (and (plusp scale)
                             (> (length digits) 1)
                             (char= (char digits (1- (length digits))) #\0))
                  do (setf digits (subseq digits 0 (1- (length digits))))
                     (decf scale))
            (when (string= digits "0") (setf scale 0))
            (dec-guard (dec-make neg digits scale) at))
          (dec-guard (dec-make neg
                               (if (>= (cmp-abs (add-abs r r) d) 0) (add-abs q "1") q)
                               +div-scale+)
                     at)))))

;;; Remainder of truncated division: takes the sign of the dividend.
(defun dec-mod (a b &optional at)
  (when (dec-zerop b) (fail "E_DIV_ZERO" "modulo by zero" at))
  (multiple-value-bind (aa bb s) (dec-aligned a b)
    (multiple-value-bind (q r) (divmod-abs aa bb)
      (declare (ignore q))
      (dec-make (dec-neg a) r s))))

;;; --- rounding. Every rounding in SEL is half away from zero (§4.4). ---------

(defun dec-round (d n &optional at)
  (if (>= n (dec-scale d))
      (dec-guard (dec-make (dec-neg d) (scale-up (dec-digits d) (- n (dec-scale d))) n) at)
      (let ((p (pow10 (- (dec-scale d) n))))
        (multiple-value-bind (q r) (divmod-abs (dec-digits d) p)
          ;; Rounding down still carries: 9.99 to one place is 10.0, a digit wider.
          (dec-guard (dec-make (dec-neg d)
                               (if (>= (cmp-abs (add-abs r r) p) 0) (add-abs q "1") q)
                               n)
                     at)))))

(defun dec-trunc (d)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (divmod-abs (dec-digits d) (pow10 (dec-scale d)))
        (declare (ignore r))
        (dec-make (dec-neg d) q 0))))

(defun dec-floor (d)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (divmod-abs (dec-digits d) (pow10 (dec-scale d)))
        (dec-make (dec-neg d)
                  (if (and (dec-neg d) (not (string= r "0"))) (add-abs q "1") q)
                  0))))

(defun dec-ceil (d)
  (if (zerop (dec-scale d))
      d
      (multiple-value-bind (q r) (divmod-abs (dec-digits d) (pow10 (dec-scale d)))
        (dec-make (dec-neg d)
                  (if (and (not (dec-neg d)) (not (string= r "0"))) (add-abs q "1") q)
                  0))))

;;; N must be a non-negative integer; the result scale is scale(x) * n, which
;;; falls out of repeated multiplication.
(defun dec-power (a n &optional at)
  (let ((result (dec-make nil "1" 0))
        (base a)
        (e n))
    (loop while (plusp e)
          do (when (oddp e) (setf result (dec-mul result base at)))
             (setf e (ash e -1))
             (when (plusp e) (setf base (dec-mul base base at))))
    result))

;;; Truncates towards zero and converts to a CL integer. Bignums make this exact
;;; without the saturation the other hosts need.
(defun dec-to-int (d)
  (let ((tr (dec-trunc d)))
    (* (if (dec-neg tr) -1 1) (parse-integer (dec-digits tr)))))
