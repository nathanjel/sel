;;;; Drives examples/order-validation.sel through the Common Lisp host API and
;;;; prints a canonical report. The other hosts' e2e drivers do the same through
;;;; theirs, and tools/e2e.sh diffs every implementation's output.
;;;;
;;;; This is the test that exercises what the project is actually for — everything
;;;; else tests the language, this tests the promise.
;;;;
;;;; Note that prices are strings, not CL numbers. A ratio would lose the scale
;;;; (2.50 and 5/2 are the same ratio and different SEL values) and a float has no
;;;; exact decimal form at all, so the host boundary is where that is said out
;;;; loud: 0.10 + 0.20 must be 0.30 on every host.

(in-package #:sel-cli)

(defun scenario (customer postcode credit-limit items)
  "ITEMS is a list of (sku qty price). Built with the same API the interpreter
uses — there is no second representation of state."
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "CUSTOMER" (sel:make-text customer))
    (sel:value-set ctx "POSTCODE" (sel:make-text postcode))
    (sel:value-set ctx "CREDIT_LIMIT" (sel:make-text credit-limit))
    (sel:value-set ctx "ITEMS"
                   ;; SEL lists are keyed from 1, so ITEMS[1] means the first line
                   ;; on every host.
                   (sel:make-list-value
                    (loop for (sku qty price) in items
                          collect (let ((item (sel:make-none)))
                                    (sel:value-set item "SKU" (sel:make-text sku))
                                    (sel:value-set item "QTY" (sel:make-text qty))
                                    (sel:value-set item "PRICE" (sel:make-text price))
                                    item))))
    ctx))

(defparameter *scenarios*
  `(("valid order" "Zażółć Gęślą" "31-874" "1000.00"
                   (("AB-1234" "3" "19.99") ("CD-5678" "1" "5.01")))
    ("blank customer" "   " "31-874" "1000.00" (("AB-1234" "1" "1.00")))
    ("bad postcode" "Anna" "318744" "1000.00" (("AB-1234" "1" "1.00")))
    ("no lines" "Anna" "31-874" "1000.00" ())
    ("zero quantity" "Anna" "31-874" "1000.00"
                     (("AB-1234" "1" "1.00") ("CD-5678" "0" "2.00")))
    ("malformed sku" "Anna" "31-874" "1000.00" (("oops" "1" "1.00")))
    ("over credit limit" "Anna" "31-874" "10.00" (("AB-1234" "3" "19.99")))
    ("exact-cent arithmetic" "Anna" "31-874" "0.30"
                             (("AB-1234" "1" "0.10") ("CD-5678" "1" "0.20")))))

(defun pad-right (s width)
  (if (>= (length s) width)
      s
      (concatenate 'string s (make-string (- width (length s)) :initial-element #\Space))))

(defun main ()
  (let ((program (sel:compile-source (read-text-file "examples/order-validation.sel"))))
    (format t "dependencies:~{ ~a~}~%" (sel:dependencies program))
    (dolist (s *scenarios*)
      (destructuring-bind (name customer postcode credit-limit items) s
        (let ((result
                (handler-case
                    (sel:value-dump
                     (sel:run program (scenario customer postcode credit-limit items)))
                  (sel:sel-error (e)
                    (format nil "!~a@~d:~d" (sel:sel-error-code e)
                            (sel:sel-error-line e) (sel:sel-error-col e)))
                  (error (e) (format nil "!HOST ~a" e)))))
          (format t "~a ~a~%" (pad-right name 24) result))))
    (sb-ext:exit :code 0)))
