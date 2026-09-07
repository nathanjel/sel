;;;; Plain usage — calling SEL from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/plain/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; The four files beside this one do the same thing through their own host API
;;;; and print byte-identical output; tools/check-examples.sh diffs them. That is
;;;; the point of the example as much as the code is: the differences you see
;;;; between these files are the languages', never SEL's.
;;;;
;;;; The visible difference here is that a context is built child by child rather
;;;; than converted from a native map. SEL's value model is ordered and 1-based
;;;; and an alist is neither, so this host says the structure out loud.

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

(defun ctx-of (pairs)
  "A context from (name . text) pairs, in order."
  (let ((ctx (sel:make-none)))
    (loop for (name . text) in pairs
          do (sel:value-set ctx name (sel:make-text text)))
    ctx))

(defun main ()
  ;; 1 — evaluate something ----------------------------------------------------

  (format t "1. one-off~%")
  (format t "   2.50 + 2.50 => ~a~%" (sel:as-text (sel:evaluate "2.50 + 2.50")))

  ;; 2 — compile once, run per request -----------------------------------------
  ;; Parsing is cheap but not free, and a program is immutable and reusable.

  (format t "2. compile once, run many~%")
  (let ((rule (sel:compile-source "IF(QTY * PRICE > LIMIT, \"over budget\", \"ok\")")))
    (loop for (qty price) in '(("3" "19.99") ("1" "5.00"))
          do (format t "   QTY=~a PRICE=~a => ~a~%" qty price
                     (sel:as-text
                      (sel:run rule (ctx-of `(("QTY" . ,qty) ("PRICE" . ,price)
                                              ("LIMIT" . "50.00"))))))))

  ;; 3 — building a context ------------------------------------------------------
  ;; Money is TEXT, never a CL number. A ratio loses the scale (2.50 and 5/2 are
  ;; the same ratio and different SEL values) and a float has no exact decimal
  ;; form at all, so the host boundary is where that is said out loud.

  (format t "3. structured context~%")
  (let ((order (sel:make-none)))
    (sel:value-set order "CUSTOMER" (sel:make-text "Zażółć"))
    (sel:value-set order "ITEMS"
                   ;; SEL lists are keyed from 1, so ITEMS[1] is the first line
                   ;; on every host.
                   (sel:make-list-value
                    (loop for (sku qty price) in '(("AB-1234" "3" "19.99")
                                                   ("CD-5678" "1" "5.01"))
                          collect (ctx-of `(("SKU" . ,sku) ("QTY" . ,qty)
                                            ("PRICE" . ,price))))))
    (format t "   first SKU => ~a~%"
            (sel:as-text (sel:run (sel:compile-source "ITEMS[1][\"SKU\"]") order)))
    (format t "   total     => ~a~%"
            (sel:as-text (sel:run (sel:compile-source
                                   "SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])") order))))
  (format t "   0.10+0.20 => ~a~%" (sel:as-text (sel:evaluate "0.10 + 0.20")))

  ;; 4 — reading results back -----------------------------------------------------
  ;; A result is a value: a scalar, children, both or neither.

  (format t "4. reading results~%")
  (let ((v (sel:evaluate "SPLIT(\"a,b,c\", \",\")")))
    (format t "   size   => ~D~%" (sel:value-size v))
    (format t "   keys   => ~{~a~^,~}~%" (sel:value-keys v))
    (format t "   [2]    => ~a~%" (sel:as-text (sel:value-get v "2")))
    (format t "   scalar => ~a~%" (sel:as-text v)))     ; scalar context: first child
  ;; A host bool prints differently in all five languages (true/1/True/T), and
  ;; this file's output has to be byte-identical to its four siblings, so say it
  ;; in SEL's own spelling rather than the host's.
  (format t "   bool   => ~a~%" (if (sel:as-bool (sel:evaluate "1 < 2")) "TRUE" "FALSE"))

  ;; 5 — the context is mutated, so rules hand values back -------------------------

  (format t "5. variables the rule set~%")
  (let ((ctx (ctx-of '(("QTY" . "3") ("PRICE" . "19.99")))))
    (sel:run (sel:compile-source
              "NET = QTY * PRICE; VAT = ROUND(NET * 0.23, 2); GROSS = NET + VAT")
             ctx)
    (dolist (name '("NET" "VAT" "GROSS"))
      (format t "   ~5a => ~a~%" name (sel:as-text (sel:value-get ctx name)))))

  ;; 6 — errors ---------------------------------------------------------------------
  ;; Every failure is a SEL-ERROR carrying a stable code and the position of the
  ;; node that actually failed. Assert on the code, never on the message.

  (format t "6. errors~%")
  (dolist (src '("3 + \"A\"" "NOSUCH(1)" "IF(1, \"a\", \"b\")" "ABORT(\"no stock\")"))
    (handler-case
        (progn (sel:evaluate src)
               (format t "   ~17a => no error~%" src))
      (sel:sel-error (e)
        (format t "   ~17a => ~a at ~D:~D~%" src
                (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))))

  ;; 7 — which fields does this rule read? ---------------------------------------------
  ;; Found statically, without running it.

  (format t "7. dependencies~%")
  (format t "   ~{~a~^ ~}~%"
          (sel:dependencies
           (sel:compile-source "T = SUM(ITEMS, _[\"QTY\"]); T > LIMIT AND CUSTOMER $!= \"\"")))
  0)
