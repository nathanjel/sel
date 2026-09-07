;;;; Complex usage — the language's reach, from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/complex/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; examples/plain/ is the API. This is the language: aggregates, named binders,
;;;; text and regex, structured results, and a rule that refuses. The four files
;;;; beside this one print byte-identical output; tools/check-examples.sh diffs
;;;; them.
;;;;
;;;; As in examples/plain/lisp.lisp the order is built child by child rather than
;;;; converted from a native map: SEL's value model is ordered and 1-based, and
;;;; an alist is neither.

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

(defun make-order ()
  "The one order every section below is asked about."
  (let ((order (ctx-of '(("CUSTOMER" . "Zażółć Gęślą")
                         ("POSTCODE" . "31-874")
                         ("CREDIT_LIMIT" . "100.00")))))
    (sel:value-set order "ITEMS"
                   (sel:make-list-value
                    (loop for (sku qty price) in '(("AB-1234" "3" "19.99")
                                                   ("CD-5678" "1" "5.01")
                                                   ("EF-9012" "2" "0.50"))
                          collect (ctx-of `(("SKU" . ,sku) ("QTY" . ,qty)
                                            ("PRICE" . ,price))))))
    order))

(defun ask (order src)
  "Compile SRC, run it against ORDER, read the answer as text."
  (sel:as-text (sel:run (sel:compile-source src) order)))

(defun main ()
  (let ((order (make-order)))

    ;; 1 — a rule set, not an expression ---------------------------------------
    ;; `;` separates statements and the last one is the answer. Intermediate
    ;; names are ordinary variables, so a long rule reads top to bottom.

    (format t "1. a rule set~%")
    (format t "   ~a~%"
            (ask order
                 (format nil "~{~a~^; ~}"
                         '("NET   = SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])"
                           "VAT   = ROUND(NET * 0.23, 2)"
                           "GROSS = NET + VAT"
                           "IF(GROSS > CREDIT_LIMIT, \"refer: \" & GROSS, \"accept: \" & GROSS)"))))

    ;; 2 — aggregates ------------------------------------------------------------
    ;; No loops. A body expression is evaluated once per element with `_` bound
    ;; to the element and `_K` to its key.

    (format t "2. aggregates~%")
    (format t "   lines        => ~a~%" (ask order "COUNT(ITEMS)"))
    (format t "   net          => ~a~%" (ask order "SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"])"))
    (format t "   all in stock => ~a~%"
            (ask order "IF(ALL(ITEMS, _[\"QTY\"] > 0), \"TRUE\", \"FALSE\")"))
    (format t "   any > 10     => ~a~%"
            (ask order "IF(ANY(ITEMS, _[\"PRICE\"] > 10.00), \"TRUE\", \"FALSE\")"))
    (format t "   dearest      => ~a~%" (ask order "MAX(MAP(ITEMS, _[\"PRICE\"]))"))

    ;; 3 — MAP renumbers, FILTER keeps the keys -----------------------------------
    ;; A filtered list stays addressable the way its source was, which is why the
    ;; dump below has holes in it. That is the contract, not an accident.

    (format t "3. map and filter~%")
    (format t "   skus         => ~a~%" (ask order "JOIN(MAP(ITEMS, _[\"SKU\"]), \", \")"))
    (format t "   bulk keys    => ~{~a~^,~}~%"
            (sel:value-keys
             (sel:run (sel:compile-source "FILTER(ITEMS, _[\"QTY\"] > 1)") order)))
    (format t "   keyed        => ~a~%"
            (sel:value-dump
             (sel:run (sel:compile-source
                       "MAP(FILTER(ITEMS, _[\"QTY\"] > 1), _K & \":\" & _[\"SKU\"])")
                      order)))

    ;; 4 — naming the binder, for nesting ------------------------------------------
    ;; `_` is the innermost element. The three-argument form names it instead,
    ;; which is the only way an outer element stays reachable from an inner body.

    (format t "4. named binders~%")
    (format t "   ~a~%"
            (sel:as-text
             (sel:evaluate
              "R[1] = (1, 2); R[2] = (3, 4); IF(ALL(R, ROW, ALL(ROW, _ > 0)), \"all positive\", \"no\")")))

    ;; 5 — text and regex ------------------------------------------------------------
    ;; Patterns are a portable subset, checked at compile time: a regex that
    ;; would mean different things on different hosts is refused rather than
    ;; guessed at.

    (format t "5. text and regex~%")
    ;; UPPER and LOWER touch A-Z and nothing else, by specification -- so the ż and
    ;; ę below come back unchanged. That is not a shortcoming, it is the only way
    ;; five hosts can agree. Measured on a sharp s: JS's toUpperCase and Python's
    ;; str.upper both answer SS, PHP's strtoupper answers ß, and C's toupper cannot
    ;; see it at all. SEL answers ß on all five, because it never asks the host.
    (format t "   upper        => ~a~%" (ask order "UPPER(CUSTOMER)"))
    (format t "   initials     => ~a~%"
            (ask order "JOIN(MAP(SPLIT(CUSTOMER, \" \"), LEFT(_, 1)), \".\")"))
    (format t "   postcode     => ~a~%"
            (ask order "IF(RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE), \"ok\", \"bad\")"))
    ;; RGROUPS puts the WHOLE match at "1", so the first capture is "2".
    (format t "   area         => ~a~%" (ask order "RGROUPS('^([0-9]{2})-', POSTCODE)[\"2\"]"))
    (format t "   padded       => ~a~%" (ask order "PADL(COUNT(ITEMS), 3, \"0\")"))

    ;; 6 — asking whether a key is there ------------------------------------------------

    (format t "6. presence~%")
    (format t "   HAS SKU      => ~a~%"
            (ask order "IF(HAS(ITEMS[1], \"SKU\"), \"TRUE\", \"FALSE\")"))
    (format t "   HAS NOTE     => ~a~%"
            (ask order "IF(HAS(ITEMS[1], \"NOTE\"), \"TRUE\", \"FALSE\")"))
    (format t "   missing      => ~a~%"
            (handler-case (ask order "ITEMS[1][\"NOTE\"]")
              (sel:sel-error (e)
                (format nil "~a at ~D:~D"
                        (sel:sel-error-code e) (sel:sel-error-line e)
                        (sel:sel-error-col e)))))

    ;; 7 — a rule that refuses -----------------------------------------------------------
    ;; ABORT is how a rule says "this is not valid", as distinct from "this could
    ;; not be computed". Both arrive as the same error type, told apart by the
    ;; code.

    (format t "7. business refusal~%")
    (loop for (label src) in
          '(("under limit"
             "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > CREDIT_LIMIT, ABORT(\"over credit limit\"), \"ok\")")
            ("over limit"
             "IF(SUM(ITEMS, _[\"QTY\"] * _[\"PRICE\"]) > 50.00, ABORT(\"over credit limit\"), \"ok\")")
            ("blank name"
             "IF(TRIM(\"   \") $== \"\", ABORT(\"customer required\"), \"ok\")"))
          do (handler-case
                 (format t "   ~11a => ~a~%" label (ask order src))
               (sel:sel-error (e)
                 (format t "   ~11a => ~a: ~a~%" label
                         (sel:sel-error-code e) (sel:sel-error-message e)))))

    ;; 8 — where a failure actually happened ---------------------------------------------
    ;; The position is the node that failed, not the statement or the call that
    ;; contains it. That is what makes a long rule debuggable.

    (format t "8. error positions~%")
    (dolist (src '("1 + ROUND(2 + \"x\", 2)" "SUM(ITEMS, _[\"QTY\"] * _[\"NOPE\"])"))
      (handler-case (sel:run (sel:compile-source src) order)
        (sel:sel-error (e)
          (format t "   ~a at ~D:~D  ~a~%"
                  (sel:sel-error-code e) (sel:sel-error-line e)
                  (sel:sel-error-col e) src)))))
  0)
