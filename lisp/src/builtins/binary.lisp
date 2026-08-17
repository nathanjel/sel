(in-package #:sel)

(define-builtin "BLEN" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-int (length (args-bytes a 0)))))

(define-builtin "TO_UTF8" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (make-bin (args-bytes a 0))))

(define-builtin "FROM_UTF8" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (%text (decode-utf8 (args-bytes a 0) (args-pos-of a 0)))))

(define-builtin "TO_HEX" 1 1
  (lambda (a ctx) (declare (ignore ctx)) (%text (bytes-to-hex (args-bytes a 0)))))

(define-builtin "FROM_HEX" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((s (args-text a 0))
          (at (args-pos-of a 0)))
      (unless (evenp (length s))
        (fail "E_BAD_ARG" "FROM_HEX needs an even number of digits" at))
      (let ((out (make-array (floor (length s) 2) :element-type '(unsigned-byte 8))))
        (loop for i from 0 below (length out)
              for hi = (ascii-hex-value (char s (* i 2)))
              for lo = (ascii-hex-value (char s (1+ (* i 2))))
              do (unless (and hi lo)
                   (fail "E_BAD_ARG"
                         (format nil "FROM_HEX: ~s is not hex" (subseq s (* i 2) (+ 2 (* i 2))))
                         at))
                 (setf (aref out i) (+ (* hi 16) lo)))
        (make-bin out)))))

(defparameter +b64-alphabet+
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")

(define-builtin "ENCODE_BASE64" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((b (args-bytes a 0)))
      (%text (with-output-to-string (out)
               (loop for i from 0 below (length b) by 3
                     for n = (logior (ash (aref b i) 16)
                                     (ash (if (< (+ i 1) (length b)) (aref b (+ i 1)) 0) 8)
                                     (if (< (+ i 2) (length b)) (aref b (+ i 2)) 0))
                     do (write-char (char +b64-alphabet+ (logand (ash n -18) 63)) out)
                        (write-char (char +b64-alphabet+ (logand (ash n -12) 63)) out)
                        (write-char (if (< (+ i 1) (length b))
                                        (char +b64-alphabet+ (logand (ash n -6) 63))
                                        #\=)
                                    out)
                        (write-char (if (< (+ i 2) (length b))
                                        (char +b64-alphabet+ (logand n 63))
                                        #\=)
                                    out)))))))

;;; Strict: padding is required and any character outside the alphabet fails.
(define-builtin "DECODE_BASE64" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((s (args-text a 0))
          (at (args-pos-of a 0))
          (out (make-array 0 :element-type '(unsigned-byte 8)
                             :adjustable t :fill-pointer 0)))
      (unless (zerop (mod (length s) 4))
        (fail "E_BAD_ARG" "DECODE_BASE64 needs a length that is a multiple of 4" at))
      (loop for i from 0 below (length s) by 4
            do (let ((quad (make-array 4 :initial-element 0))
                     (padding 0))
                 (loop for k from 0 below 4
                       for ch = (char s (+ i k))
                       do (cond
                            ((char= ch #\=)
                             (when (or (< (+ i 4) (length s)) (< k 2))
                               (fail "E_BAD_ARG" "misplaced base64 padding" at))
                             (incf padding))
                            (t
                             (when (plusp padding)
                               (fail "E_BAD_ARG" "misplaced base64 padding" at))
                             (let ((v (position ch +b64-alphabet+)))
                               (unless v
                                 (fail "E_BAD_ARG"
                                       (format nil "invalid base64 character ~s" (string ch))
                                       at))
                               (setf (aref quad k) v)))))
                 (let ((n (logior (ash (aref quad 0) 18) (ash (aref quad 1) 12)
                                  (ash (aref quad 2) 6) (aref quad 3))))
                   (vector-push-extend (logand (ash n -16) 255) out)
                   (when (< padding 2) (vector-push-extend (logand (ash n -8) 255) out))
                   (when (< padding 1) (vector-push-extend (logand n 255) out)))))
      (make-bin (coerce out '(vector (unsigned-byte 8)))))))

;;; CRC-32/ISO-HDLC: reflected, polynomial #xEDB88320, init and final xor all ones.
(defparameter +crc32-table+
  (let ((table (make-array 256 :element-type '(unsigned-byte 32))))
    (dotimes (i 256 table)
      (let ((c i))
        (dotimes (k 8)
          (setf c (if (logbitp 0 c)
                      (logxor #xedb88320 (ash c -1))
                      (ash c -1))))
        (setf (aref table i) c)))))

(define-builtin "CRC32" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((crc #xffffffff))
      (loop for b across (args-bytes a 0)
            do (setf crc (logxor (aref +crc32-table+ (logand (logxor crc b) 255))
                                 (ash crc -8))))
      (%text (string-downcase (format nil "~8,'0x" (logxor crc #xffffffff)))))))

(define-builtin "BTL" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (make-list-value (loop for b across (args-bytes a 0) collect (make-int b)))))

(define-builtin "LTB" 1 1
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((v (args-val a 0))
           (at (args-pos-of a 0))
           (items (if (plusp (value-size v)) (value-values v) (list v)))
           (out (make-array (length items) :element-type '(unsigned-byte 8))))
      (loop for item in items
            for i from 0
            do (let* ((d (as-dec item at))
                      (n (dec-to-int d)))
                 (when (or (/= (dec-scale d) 0) (minusp n) (> n 255))
                   (fail "E_RANGE" (format nil "LTB element ~d is not a byte value" (1+ i)) at))
                 (setf (aref out i) n)))
      (make-bin out))))
