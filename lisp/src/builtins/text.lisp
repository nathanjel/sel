;;;; Text built-ins. A CL character is a code point, so LENGTH and SUBSEQ already
;;;; count what SEL counts — never bytes, never UTF-16 units. Positions are
;;;; 1-based and 0 means "not found" (§7.5).

(in-package #:sel)

(defun cp-subseq (s from &optional to)
  "SUBSEQ with the bounds clamped, so a length past the end shortens rather than
signalling — LEFT and SUBSTR are specified to return fewer characters."
  (let* ((n (length s))
         (a (max 0 (min from n)))
         (b (if to (max a (min to n)) n)))
    (subseq s a b)))

(define-builtin "LEN" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (length (args-text a 0)))))

(define-builtin "LEFT" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (%text (cp-subseq (args-text a 0) 0 (args-non-neg-int a 1)))))

(define-builtin "RIGHT" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((s (args-text a 0))
           (n (args-non-neg-int a 1)))
      (%text (cp-subseq s (- (length s) n) (length s))))))

(define-builtin "SUBSTR" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((s (args-text a 0))
          (start (args-int a 1)))
      (when (< start 1)
        (fail "E_RANGE" "SUBSTR start is 1-based and must be at least 1" (args-pos-of a 1)))
      (let ((from (1- start)))
        (%text (if (= (args-count a) 2)
                   (cp-subseq s from)
                   (cp-subseq s from (+ from (args-non-neg-int a 2)))))))))

(define-builtin "FIND" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((needle (args-text a 0))
          (hay (args-text a 1))
          (from 0))
      (when (= (args-count a) 3)
        (let ((f (args-int a 2)))
          (when (< f 1)
            (fail "E_RANGE" "FIND start is 1-based and must be at least 1" (args-pos-of a 2)))
          (setf from (1- f))))
      (when (zerop (length needle))
        (fail "E_BAD_ARG" "FIND needle must not be empty" (args-pos-of a 0)))
      (make-int (let ((at (and (<= from (length hay))
                               (search needle hay :start2 from))))
                  (if at (1+ at) 0))))))

(define-builtin "REPLACE" 3 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((needle (args-text a 0))
          (repl (args-text a 1))
          (hay (args-text a 2)))
      (when (zerop (length needle))
        (fail "E_BAD_ARG" "REPLACE needle must not be empty" (args-pos-of a 0)))
      ;; All occurrences, left to right, non-overlapping. The result's length is
      ;; worked out as the occurrences are found, and the call refused the moment
      ;; it passes the cap, before the text is built (SPEC 6.4).
      (let ((size (length hay))
            (i 0)
            (found '()))
        (loop for at = (search needle hay :start2 i)
              while at
              do (push at found)
                 (setf i (+ at (length needle)))
                 (incf size (- (length repl) (length needle)))
                 (check-text-cap size (args-pos a)))
        (%text (with-output-to-string (out)
                 (let ((i 0))
                   (dolist (at (nreverse found))
                     (write-string hay out :start i :end at)
                     (write-string repl out)
                     (setf i (+ at (length needle))))
                   (write-string hay out :start i))))))))

(define-builtin "SPLIT" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((hay (args-text a 0))
          (sep (args-text a 1)))
      (when (zerop (length sep))
        (fail "E_BAD_ARG" "SPLIT separator must not be empty" (args-pos-of a 1)))
      (let ((parts '())
            (count 0)
            (i 0))
        (loop for at = (search sep hay :start2 i)
              while at
              do (check-collection-cap (incf count) (args-pos a))
                 (push (%text (subseq hay i at)) parts)
                 (setf i (+ at (length sep))))
        (check-collection-cap (1+ count) (args-pos a))
        (push (%text (subseq hay i)) parts)
        (make-list-value (nreverse parts))))))

(defun sel-space-char-p (c)
  (member (char-code c) '(#x20 #x09 #x0d #x0a)))

(defun trim-text (s left right)
  (let ((a 0)
        (b (length s)))
    (when left (loop while (and (< a b) (sel-space-char-p (char s a))) do (incf a)))
    (when right (loop while (and (> b a) (sel-space-char-p (char s (1- b)))) do (decf b)))
    (subseq s a b)))

(define-builtin "TRIM" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (trim-text (args-text a 0) t t))))
(define-builtin "LTRIM" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (trim-text (args-text a 0) t nil))))
(define-builtin "RTRIM" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (trim-text (args-text a 0) nil t))))

;;; ASCII only, deliberately. CL's STRING-UPCASE applies full Unicode case
;;; mapping, which cannot be reconciled with the other hosts without shipping a
;;; case table; guessing would break the invariant silently rather than loudly.
(defun ascii-case (s up)
  (map 'string
       (lambda (c)
         (let ((n (char-code c)))
           (cond ((and up (<= 97 n 122)) (code-char (- n 32)))
                 ((and (not up) (<= 65 n 90)) (code-char (+ n 32)))
                 (t c))))
       s))

(define-builtin "UPPER" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (ascii-case (args-text a 0) t))))
(define-builtin "LOWER" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (ascii-case (args-text a 0) nil))))

(define-builtin "BACKWARDS" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (reverse (args-text a 0)))))

(define-builtin "REPEAT" 2 2
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((s (args-text a 0))
          (n (args-non-neg-int a 1)))
      ;; Empty text repeated any number of times is empty, at once: only a result
      ;; that would be too long is refused, and a count is not a result.
      (if (or (zerop n) (zerop (length s)))
          (%text "")
          (let ((total (* n (length s))))
            (check-text-cap total (args-pos a))
            (let ((out (make-string total))
                  (filled (length s)))
              ;; One copy of the unit, then the filled prefix doubles into the
              ;; rest (the two regions never overlap): a REPLACE call per copy
              ;; costs more than the characters it moves (LISP-P21).
              (replace out s)
              (loop while (< filled total)
                    do (let ((n (min filled (- total filled))))
                         (replace out out :start1 filled :start2 0 :end2 n)
                         (incf filled n)))
              (%text out)))))))

(defun pad-text (a left)
  (let* ((s (args-text a 0))
         (width (args-non-neg-int a 1))
         (fill (args-text a 2)))
    (when (zerop (length fill))
      (fail "E_BAD_ARG" "pad fill must not be empty" (args-pos-of a 2)))
    (if (>= (length s) width)
        (%text s)
        ;; The result is allocated once, at its final length, and the padding is
        ;; written straight into it: building the padding in a string stream and
        ;; then copying it out and concatenating cost about 14 characters of garbage
        ;; per result character (LISP-P21).
        (let* ((need (progn (check-text-cap width (args-pos a)) (- width (length s))))
               (flen (length fill))
               (out (make-string width))
               (pad-at (if left 0 (length s))))
          (if left
              (replace out s :start1 need)
              (replace out s))
          (if (= flen 1)
              (fill out (char fill 0) :start pad-at :end (+ pad-at need))
              (loop for i from 0 below need
                    for j = 0 then (if (= (1+ j) flen) 0 (1+ j))
                    do (setf (char out (+ pad-at i)) (char fill j))))
          (%text out)))))

(define-builtin "PADL" 3 3 (lambda (a ctx) (declare (ignore ctx)) (pad-text a t)))
(define-builtin "PADR" 3 3 (lambda (a ctx) (declare (ignore ctx)) (pad-text a nil)))

(define-builtin "CHAR" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((n (args-int a 0)))
      (when (or (minusp n) (> n #x10ffff) (<= #xd800 n #xdfff))
        (fail "E_RANGE" (format nil "~d is not an encodable code point" n) (args-pos-of a 0)))
      (%text (string (code-char n))))))

(define-builtin "CODE" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((s (args-text a 0)))
      (when (zerop (length s))
        (fail "E_RANGE" "CODE of empty text" (args-pos-of a 0)))
      (make-int (char-code (char s 0))))))
