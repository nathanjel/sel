;;;; The application's own functions, in memory and in PostgreSQL — Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-functions         (starts the databases for you)
;;;;
;;;; The application registers five functions of its own. Each has a local
;;;; implementation — the code REGISTER-FUNCTION runs — and four also get a SQL
;;;; spelling for PostgreSQL, which the application promises computes the same
;;;; thing (spec §8.1, sql/MAP.md §4.7):
;;;;
;;;;   SLUG(title)                 a plain value mapping        -> slug(), an SQL function
;;;;   MARGIN_PCT(price, cost)     two numbers in, one out      -> margin_pct(), an SQL function
;;;;   VAT_RATE(country, category) a lookup in a table          -> vat_rate(), reads vat_rates
;;;;   SHIPPING_COST(kg, country)  a stored function with logic -> shipping_cost(), PL/pgSQL
;;;;   HAS_TAG(tags, tag)          a list argument              -> an inline ANY(ARRAY[...])
;;;;   WORDS(title)                returns a list               -> no spelling: stays in memory
;;;;
;;;; Every pipeline prints its plan and its rows, and whether those rows are the
;;;; rows the same program computes in memory — which is how the example checks
;;;; that the two implementations of each function agree on this data.
;;;;
;;;; The four files beside this one print byte-identical output.

;;; Before the DEFPACKAGE, because --load reads and evaluates one top-level form
;;; at a time: the SEL.SQL and SEL-DB symbols further down are only readable once
;;; the packages they name exist.
(let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))
(load (merge-pathnames "../lib/db.lisp" *load-truename*))

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

(defparameter *conn* (sel-db:connect "postgresql"))

;;; 1 — the local implementations ----------------------------------------------
;;; What REGISTER-FUNCTION runs: plain code, or — where exact decimal arithmetic
;;; matters — a SEL expression, so the local answer has SEL's numbers.

;; EXAMPLE-BEGIN local
(defun slug (args)
  (let ((out (make-string-output-stream)) (dash nil) (empty t))
    (loop for c across (sel:args-text args 0)
          for lc = (if (char<= #\A c #\Z) (code-char (+ (char-code c) 32)) c) ; ASCII only, as SQL's
          do (cond ((or (char<= #\a lc #\z) (char<= #\0 lc #\9))       ; [^a-z0-9]+ sees it
                    (when (and dash (not empty)) (write-char #\- out))
                    (write-char lc out)
                    (setf dash nil empty nil))
                   (t (setf dash t))))
    (sel:make-text (get-output-stream-string out))))

(defparameter *margin* (sel:compile-source "ROUND((PRICE - COST) * 100 / PRICE, 1)"))

(defun margin-pct (args)
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "PRICE" (sel:args-val args 0))
    (sel:value-set ctx "COST" (sel:args-val args 1))
    (sel:run *margin* ctx)))

(defparameter *rates*
  (let ((rates (make-hash-table :test #'equal)))
    (dolist (r (sel:value-values (sel-db:query *conn* "SELECT * FROM vat_rates")) rates)
      (setf (gethash (list (sel:as-text (sel:value-get r "country"))
                           (sel:as-text (sel:value-get r "category")))
                     rates)
            (sel:value-get r "rate")))))

(defun vat-rate (args)
  (let* ((country (sel:args-text args 0))
         (category (sel:args-text args 1))
         ;; OR is safe here: a missing rate is NIL, and a SEL value never is.
         (rate (or (gethash (list country category) *rates*)
                   (gethash (list country "*") *rates*))))
    (if rate (sel:value-copy rate) (sel:make-text "0"))))

(defparameter *shipping*
  (sel:compile-source
   (concatenate 'string "COND(KG <= 1, 4.90, KG <= 5, 9.90, KG <= 20, 19.90, 49.00)"
                " * IF(COUNTRY $== \"PL\", 1, 2)")))

(defun shipping-cost (args)
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "KG" (sel:args-val args 0))
    (sel:value-set ctx "COUNTRY" (sel:args-val args 1))
    (sel:run *shipping* ctx)))

(defun has-tag (args)
  (let* ((tags (sel:args-val args 0))
         (tag (sel:args-text args 1))
         (members (if (plusp (sel:value-size tags))
                      (sel:value-values tags)
                      (list tags))))                    ; a scalar is a list of one
    (sel:make-bool (some (lambda (v) (string= (sel:as-text v) tag)) members))))

(defun words (args)
  (let ((out (sel:make-none)))
    (dolist (w (uiop:split-string (sel:as-text (slug args)) :separator "-") out)
      (when (plusp (length w))
        (sel:value-set out (format nil "~D" (1+ (sel:value-size out))) (sel:make-text w))))))

(sel:register-function "SLUG" 1 1 #'slug)
(sel:register-function "MARGIN_PCT" 2 2 #'margin-pct)
(sel:register-function "VAT_RATE" 2 2 #'vat-rate)
(sel:register-function "SHIPPING_COST" 2 2 #'shipping-cost)
(sel:register-function "HAS_TAG" 2 2 #'has-tag)
(sel:register-function "WORDS" 1 1 #'words)
;; EXAMPLE-END local

;;; 2 — the SQL spellings ------------------------------------------------------
;;; After the functions: a spelling for a name that is not registered is refused.

;; EXAMPLE-BEGIN spell
(sel.sql:define-entry "postgresql" :funcs "SLUG"
                      (list :tpl "slug({0})" :ret "TEXT" :args '("TEXT")))
(sel.sql:define-entry "postgresql" :funcs "MARGIN_PCT"
                      (list :tpl "margin_pct({0}, {1})" :ret "NUM" :args '("NUM" "NUM")))
(sel.sql:define-entry "postgresql" :funcs "VAT_RATE"
                      (list :tpl "vat_rate({0}, {1})" :ret "NUM" :args '("TEXT" "TEXT")))
(sel.sql:define-entry "postgresql" :funcs "SHIPPING_COST"
                      (list :tpl "shipping_cost({0}, {1})" :ret "NUM" :args '("NUM" "TEXT")))
(sel.sql:define-entry "postgresql" :funcs "HAS_TAG"
                      (list :tpl "({1} = ANY(ARRAY[{0}]))" :ret "BOOL" :args '("LIST" "TEXT")))
;; WORDS returns a list: no spelling can say that, so it has none.
;; EXAMPLE-END spell

(defun relation (table alias &rest fields)
  "A table as a relation binding. FIELDS alternate a column name and its kind."
  (sel.sql:binding-relation
   table alias
   (loop for (name kind) on fields by #'cddr
         collect (cons name (sel.sql:binding-column name alias kind)))))

(defparameter *schema*
  (list (cons "PRODUCTS" (relation "products" "p" "product_id" :num "title" :text
                                   "category" :text "price" :num "cost" :num
                                   "weight_kg" :num "tag1" :text "tag2" :text "tag3" :text))
        (cons "ORDERS"   (relation "orders" "o" "order_id" :num "country" :text))
        (cons "LINES"    (relation "order_lines" "l" "order_id" :num "line_no" :num
                                   "product_id" :num "qty" :num))))

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: the pipelines sit beside it, one .sel file each.")

(defun read-file (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defparameter *pipelines*
  '(("gifts with a margin of 40% or more"                . "gifts-by-margin.sel")
    ("gross revenue and shipping per country"            . "gross-per-country.sel")
    ("words in the titles of the better-margin products" . "title-words.sel"))
  "(title . file) per pipeline, in the order they run.")

;; EXAMPLE-BEGIN run
(defun run-pipeline (file conn tables)
  "Run the pipeline in FILE with as much of it in PostgreSQL as PostgreSQL can answer.
Returns the rows, the plan and the compiled program."
  (let* ((program (sel:compile-source (read-file file)))
         (plan (sel.sql:plan-hybrid program "postgresql" *schema*))
         (rows (sel.sql:execute-hybrid plan (sel-db:runner conn)
                                       (when (sel.sql:hybrid-plan-pure-memory-p plan) tables))))
    (values rows plan program)))
;; EXAMPLE-END run

(defun main ()
  (let ((tables (sel:make-none)))
    (loop for (name table key) in '(("PRODUCTS" "products" "product_id")
                                    ("ORDERS" "orders" "order_id")
                                    ("LINES" "order_lines" "order_id, line_no"))
          do (sel:value-set tables name
                            (sel-db:query *conn* (format nil "SELECT * FROM ~a ORDER BY ~a" table key))))

    (loop for (title . file) in *pipelines*
          for n from 1
          do (multiple-value-bind (rows plan program) (run-pipeline file *conn* tables)
               (format t "~D. ~a~%" n title)
               (format t "   plan        ~a~%"
                       (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                             ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                             (t "hybrid")))
               (let ((statement (sel.sql:hybrid-plan-sql-statement plan)))
                 (when statement
                   (format t "   sql         ~a~%" (sel.sql:as-statement statement))
                   (let ((caveats (sel.sql:fragment-caveats statement)))
                     (format t "   caveats     ~a~%"
                             (if caveats (format nil "~{~a~^, ~}" caveats) "(none)")))))
               (format t "~a~%" (sel-db:render rows "   | "))
               (format t "   in memory   ~a~%"
                       (if (string= (sel:value-dump (sel:run program (sel:value-copy tables)))
                                    (sel:value-dump rows))
                           "same rows"
                           "DIFFERENT")))))

  ;; 4 — what strict translation says ------------------------------------------
  ;; A spelling is the application's promise, not this layer's, so strict mode —
  ;; exact or nothing — refuses it.

  (format t "~D. strict translation~%" (1+ (length *pipelines*)))
  ;; EXAMPLE-BEGIN strict
  (let ((rule (sel:compile-source "SLUG(TITLE) $== \"cast-iron-pan\""))
        (title (list (cons "TITLE" (sel.sql:binding-column "title" "p" :text)))))
    (format t "   caveats     ~{~a~^, ~}~%"
            (sel.sql:fragment-caveats (sel.sql:translate rule "postgresql" title)))
    (handler-case (sel.sql:translate rule "postgresql" title '(:strict t))
      (sel.sql:sql-error (e)
        (format t "   strict      ~a~%" (sel.sql:sql-error-code e)))))
  ;; EXAMPLE-END strict
  0)
