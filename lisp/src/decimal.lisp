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

(defstruct (dec (:constructor %make-dec (neg digits scale)))
  (neg nil :type boolean)
  (digits "0" :type simple-string)
  (scale 0 :type fixnum))

(defun dec-make (neg digits scale)
  (let ((d (coerce digits 'simple-string)))
    (%make-dec (if (string= d "0") nil (and neg t)) d scale)))

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

(defun dec-parse (text)
  "Return a DEC, or NIL when TEXT is not a number. Callers raise E_NOT_NUM with
the position of the offending node."
  (when (and (stringp text) (dec-number-string-p text))
    (let* ((neg (char= (char text 0) #\-))
           (body (if neg (subseq text 1) text))
           (dot (position #\. body))
           (int-part (if dot (subseq body 0 dot) body))
           (frac-part (if dot (subseq body (1+ dot)) "")))
      (dec-make neg (dstrip (concatenate 'string int-part frac-part)) (length frac-part)))))

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
  (dec-make (minusp n) (format nil "~d" (abs n)) 0))

(defun dec-zerop (d) (string= (dec-digits d) "0"))
(defun dec-negate (d) (dec-make (not (dec-neg d)) (dec-digits d) (dec-scale d)))
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

(defun dec-add (a b)
  (multiple-value-bind (aa bb s) (dec-aligned a b)
    (if (eq (dec-neg a) (dec-neg b))
        (dec-make (dec-neg a) (add-abs aa bb) s)
        (let ((c (cmp-abs aa bb)))
          (cond ((zerop c) (dec-make nil "0" s))
                ((plusp c) (dec-make (dec-neg a) (sub-abs aa bb) s))
                (t (dec-make (dec-neg b) (sub-abs bb aa) s)))))))

(defun dec-sub (a b) (dec-add a (dec-negate b)))

(defun dec-mul (a b)
  (dec-make (not (eq (dec-neg a) (dec-neg b)))
            (mul-abs (dec-digits a) (dec-digits b))
            (+ (dec-scale a) (dec-scale b))))

(defun dec-cmp (a b)
  (cond
    ((and (dec-zerop a) (dec-zerop b)) 0)
    ((not (eq (dec-neg a) (dec-neg b))) (if (dec-neg a) -1 1))
    (t (multiple-value-bind (aa bb s) (dec-aligned a b)
         (declare (ignore s))
         (let ((c (cmp-abs aa bb)))
           (if (dec-neg a) (- c) c))))))

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
            (dec-make neg digits scale))
          (dec-make neg
                    (if (>= (cmp-abs (add-abs r r) d) 0) (add-abs q "1") q)
                    +div-scale+)))))

;;; Remainder of truncated division: takes the sign of the dividend.
(defun dec-mod (a b &optional at)
  (when (dec-zerop b) (fail "E_DIV_ZERO" "modulo by zero" at))
  (multiple-value-bind (aa bb s) (dec-aligned a b)
    (multiple-value-bind (q r) (divmod-abs aa bb)
      (declare (ignore q))
      (dec-make (dec-neg a) r s))))

;;; --- rounding. Every rounding in SEL is half away from zero (§4.4). ---------

(defun dec-round (d n)
  (if (>= n (dec-scale d))
      (dec-make (dec-neg d) (scale-up (dec-digits d) (- n (dec-scale d))) n)
      (let ((p (pow10 (- (dec-scale d) n))))
        (multiple-value-bind (q r) (divmod-abs (dec-digits d) p)
          (dec-make (dec-neg d)
                    (if (>= (cmp-abs (add-abs r r) p) 0) (add-abs q "1") q)
                    n)))))

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
(defun dec-power (a n)
  (let ((result (dec-make nil "1" 0))
        (base a)
        (e n))
    (loop while (plusp e)
          do (when (oddp e) (setf result (dec-mul result base)))
             (setf e (ash e -1))
             (when (plusp e) (setf base (dec-mul base base))))
    result))

;;; Truncates towards zero and converts to a CL integer. Bignums make this exact
;;; without the saturation the other hosts need.
(defun dec-to-int (d)
  (let ((tr (dec-trunc d)))
    (* (if (dec-neg tr) -1 1) (parse-integer (dec-digits tr)))))
