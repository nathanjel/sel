;;;; Form validation — one rule set, the same verdicts in every host, from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/validation/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; A checkout form's rules, one per field, written in SEL. The server compiles
;;;; them once at start-up, so a rule that does not parse fails the deployment
;;;; rather than a customer. Each rule answers "" when the field is fine and
;;;; ABORT("message") when it is not, so there are two kinds of failure and they
;;;; are told apart by code: E_ABORT is a message for the user, anything else
;;;; means the rule itself is broken and the user should never see it.
;;;; DEPENDENCIES tells a browser which rules to re-run when a field changes —
;;;; and the browser runs the very same rule text, in JavaScript.
;;;;
;;;; The four files beside this one print byte-identical output.

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

;; EXAMPLE-BEGIN rules
(defparameter *rules*
  `(("name"     . "IF(IS_BLANK(NAME), ABORT(\"Please tell us your name\"), \"\")")
    ("email"    . ,(concatenate
                    'string
                    "IF(RMATCH('^[^@ ]+@[^@ ]+\\.[a-z]{2,}$', TRIM(EMAIL), \"i\"), \"\","
                    " ABORT(\"{EMAIL} does not look like an e-mail address\"))"))
    ("postcode" . ,(concatenate
                    'string
                    "COND(COUNTRY $== \"PL\" AND NOT RMATCH('^\\d{2}-\\d{3}$', POSTCODE),"
                    "       ABORT(\"Polish postcodes look like 00-000\"),"
                    "     COUNTRY $== \"DE\" AND NOT RMATCH('^\\d{5}$', POSTCODE),"
                    "       ABORT(\"German postcodes have five digits\"),"
                    "     \"\")"))
    ("quantity" . ,(concatenate
                    'string
                    "IF(NOT ISNUM(QTY) OR QTY < 1 OR QTY > STOCK,"
                    " ABORT(\"Choose between 1 and {STOCK}\"), \"\")"))
    ("total"    . ,(concatenate
                    'string
                    "TOTAL = ROUND(QTY * PRICE * (1 - DISCOUNT), 2);"
                    " IF(TOTAL > CREDIT_LIMIT, ABORT(\"{TOTAL} is over your limit of {CREDIT_LIMIT}\"), \"\")")))
  "One rule per field, as (field . source), in the order they are checked.")
;; EXAMPLE-END rules

;;; 1 — compile once, at start-up ----------------------------------------------

;; EXAMPLE-BEGIN compile
(defparameter *compiled*
  (loop for (field . source) in *rules*
        collect (cons field (sel:compile-source source))))
;; EXAMPLE-END compile

;;; 3 — validating submissions -------------------------------------------------

;; EXAMPLE-BEGIN validate
(defun validate (form)
  "The problems with FORM, an alist of field values, as (field . message)."
  (loop for (field . rule) in *compiled*
        for verdict = (handler-case (sel:as-text (sel:run rule (sel:from-native form)))
                        (sel:sel-error (e)
                          ;; E_ABORT is the rule speaking to the user; anything
                          ;; else is a broken rule or data it cannot read — log
                          ;; it, show a generic line.
                          (if (string= (sel:sel-error-code e) "E_ABORT")
                              (sel:sel-error-message e)
                              (format nil "could not be checked (~a)" (sel:sel-error-code e)))))
        unless (string= verdict "")
          collect (cons field verdict)))
;; EXAMPLE-END validate

(defparameter *submissions*
  '((("NAME" . "Anna Nowak") ("EMAIL" . "anna@example.pl") ("COUNTRY" . "PL")
     ("POSTCODE" . "31-874") ("QTY" . "2") ("STOCK" . "5") ("PRICE" . "19.99")
     ("DISCOUNT" . "0.10") ("CREDIT_LIMIT" . "100.00"))
    (("NAME" . "   ") ("EMAIL" . "bruno(at)example.de") ("COUNTRY" . "DE")
     ("POSTCODE" . "1011") ("QTY" . "9") ("STOCK" . "5") ("PRICE" . "19.99")
     ("DISCOUNT" . "0") ("CREDIT_LIMIT" . "100.00"))
    (("NAME" . "Chloé") ("EMAIL" . "CHLOE@EXAMPLE.FR ") ("COUNTRY" . "FR")
     ("POSTCODE" . "69002") ("QTY" . "4") ("STOCK" . "5") ("PRICE" . "29.99")
     ("DISCOUNT" . "0.05") ("CREDIT_LIMIT" . "100.00"))
    (("NAME" . "Dawid") ("EMAIL" . "dawid@example.pl") ("COUNTRY" . "PL")
     ("POSTCODE" . "00-950") ("QTY" . "1") ("STOCK" . "5") ("PRICE" . "twenty")
     ("DISCOUNT" . "0") ("CREDIT_LIMIT" . "100.00"))))

(defun main ()
  (format t "1. the rule set~%")
  (loop for (field . rule) in *compiled*
        do (format t "   ~9a reads ~{~a~^ ~}~%" field (sel:dependencies rule)))

  ;; 2 — what to re-check when a field changes ---------------------------------

  (format t "2. re-check on change~%")
  (let ((watch '()))                    ; (name . fields), fields in rule order
    (loop for (field . rule) in *compiled*
          do (dolist (name (sel:dependencies rule))
               (let ((entry (assoc name watch :test #'string=)))
                 (if entry
                     (setf (cdr entry) (append (cdr entry) (list field)))
                     (push (list name field) watch)))))
    (loop for (name . fields) in (sort watch #'string< :key #'car)
          do (format t "   ~13a ~{~a~^, ~}~%" name fields)))

  (format t "3. submissions~%")
  (loop for form in *submissions*
        for n from 1
        for problems = (validate form)
        do (unless problems
             (format t "   #~D accepted~%" n))
           (loop for (field . message) in problems
                 do (format t "   #~D ~9a ~a~%" n field message)))
  0)
