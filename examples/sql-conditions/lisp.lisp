;;;; SQL conditions — one rule as a WHERE clause, from Common Lisp.
;;;;
;;;;   tools/check-usage.sh sql-conditions        (starts the databases for you)
;;;;
;;;; A rule written for the application can filter rows where they live. Part 1
;;;; is the naive integration: the host knows nothing about the schema except
;;;; that a variable is a column of the same name. Part 2 describes the schema —
;;;; types, a list of columns, a related table, a parameter — and gets SQL that is
;;;; both tighter and able to say more. Either way a rule SQL cannot express is
;;;; refused whole, and runs in memory instead; every answer below is checked
;;;; against the in-memory one.
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

(defun ids (records)
  (let ((ids (mapcar (lambda (record) (sel:as-text (sel:value-get record "id"))) records)))
    (if ids (format nil "~{~a~^, ~}" ids) "(none)")))

(defun refusal (rule dialect bindings)
  "TRANSLATE says why TRY-TRANSLATE returned nothing."
  (handler-case (progn (sel.sql:translate rule dialect bindings)
                       "translated")
    (sel.sql:sql-error (e) (sel.sql:sql-error-code e))))

(defun context-of (row)
  "A row as a rule's context: SEL names are upper case, columns are not."
  (let ((ctx (sel:make-none)))
    (loop for (column . value) in (sel:value-entries row)
          do (sel:value-set ctx (string-upcase column) value))
    ctx))

(defun same-p (rows in-memory)
  (if (string= (ids (sel:value-values rows)) (ids in-memory)) "TRUE" "FALSE"))

;;; 1 — naive: a column per variable, nothing else known ------------------------

(defparameter *rules*
  '("COUNTRY $== \"PL\" AND TIER $!= \"standard\""
    "COUNTRY $== \"PL\" AND CREDIT_LIMIT >= 1000"
    "RMATCH('^[0-9]{2}-[0-9]{3}$', POSTCODE)"
    "IS_BLANK(EMAIL) OR NOT RMATCH('^[^@ ]+@[^@ ]+$', EMAIL)"
    "ANY(SPLIT(NAME, \" \"), LEN(_) > 9)"))

;; EXAMPLE-BEGIN naive
(defun naive (conn dialect source everyone)
  "The customers SOURCE accepts: in SQL when it translates, else in memory over
EVERYONE. Returns the rows, the WHERE fragment or NIL, and what went into it."
  (let* ((rule (sel:compile-source source))
         (bindings (loop for name in (sel:dependencies rule)
                         collect (cons name (sel.sql:binding-column (string-downcase name)))))
         (where (sel.sql:try-translate rule dialect bindings))
         (rows (sel:make-none)))
    (if where
        (setf rows (sel-db:query conn (concatenate 'string "SELECT id FROM customers WHERE "
                                                   (sel.sql:as-condition where :params)
                                                   " ORDER BY id")
                                 (sel.sql:bindings where)))
        ;; refused: the rule stays in the application, over rows it loads
        (loop for (key . row) in (sel:value-entries everyone)
              when (sel:as-bool (sel:run rule (context-of row)))
                do (sel:value-set rows key row)))
    (values rows where rule bindings)))
;; EXAMPLE-END naive

;;; 2 — involved: the host describes its schema ---------------------------------

;; EXAMPLE-BEGIN involved
(defparameter *bindings*
  (list (cons "STATUS"    (sel.sql:binding-column "status" "o" :text :exact t))
        (cons "TOTAL"     (sel.sql:binding-column "total" "o" :num))
        (cons "CHANNEL"   (sel.sql:binding-column "channel" "o" :text :exact t))
        (cons "TAGS"      (sel.sql:binding-columns (sel.sql:binding-column "tag1" "o" :text)
                                                   (sel.sql:binding-column "tag2" "o" :text)
                                                   (sel.sql:binding-column "tag3" "o" :text)))
        (cons "ITEMS"     (sel.sql:binding-relation
                           "order_items" "i"
                           (list (cons "sku"   (sel.sql:binding-column "sku" "i" :text))
                                 (cons "qty"   (sel.sql:binding-column "qty" "i" :num))
                                 (cons "price" (sel.sql:binding-column "price" "i" :num)))
                           nil "\"i\".\"order_id\" = \"o\".\"id\""))
        (cons "MIN_TOTAL" (sel.sql:binding-value (sel:make-text "100.00")))))
;; EXAMPLE-END involved

(defun order-context (order items)
  "What the rule sees in memory: the same names, as values."
  (let ((ctx (sel:make-none))
        (tags (sel:make-none))
        (lines (sel:make-none)))
    (dolist (name '("status" "total" "channel"))
      (sel:value-set ctx (string-upcase name) (sel:value-get order name)))
    (loop for column in '("tag1" "tag2" "tag3")
          for n from 1
          do (sel:value-set tags (format nil "~D" n) (sel:value-get order column)))
    (sel:value-set ctx "TAGS" tags)
    (dolist (item (sel:value-values items))
      (when (string= (sel:as-text (sel:value-get item "order_id"))
                     (sel:as-text (sel:value-get order "id")))
        (sel:value-set lines (format nil "~D" (1+ (sel:value-size lines))) item)))
    (sel:value-set ctx "ITEMS" lines)
    (sel:value-set ctx "MIN_TOTAL" (sel:make-text "100.00"))
    ctx))

;; EXAMPLE-BEGIN involved-run
(defun involved (conn source)
  "The orders SOURCE accepts, asked of PostgreSQL through *BINDINGS*.
Returns the rows, the WHERE fragment and the compiled rule."
  (let* ((rule (sel:compile-source source))
         (where (sel.sql:translate rule "postgresql" *bindings*))
         (sql (concatenate 'string "SELECT id FROM orders o WHERE "
                           (sel.sql:as-condition where :params) " ORDER BY id")))
    (values (sel-db:query conn sql (sel.sql:bindings where)) where rule)))
;; EXAMPLE-END involved-run

(defun main ()
  (format t "1. naive bindings~%")
  (dolist (dialect '("sqlite" "mariadb"))
    (let* ((conn (sel-db:connect dialect))
           (everyone (sel-db:query conn "SELECT * FROM customers ORDER BY id")))
      (dolist (source *rules*)
        (multiple-value-bind (rows where rule bindings) (naive conn dialect source everyone)
          (let ((in-memory (remove-if-not (lambda (row) (sel:as-bool (sel:run rule (context-of row))))
                                          (sel:value-values everyone))))
            (format t "   ~8a ~a~%" dialect source)
            (if where
                (format t "             sql    ~a~%" (sel.sql:as-condition where))
                (format t "             memory (~a)~%" (refusal rule dialect bindings)))
            (format t "             rows   ~a | same as in memory: ~a~%"
                    (ids (sel:value-values rows)) (same-p rows in-memory)))))))

  (format t "2. described bindings~%")
  (let* ((conn (sel-db:connect "postgresql"))
         (orders (sel-db:query conn "SELECT * FROM orders ORDER BY id"))
         (items (sel-db:query conn "SELECT * FROM order_items ORDER BY order_id, line_no")))
    (dolist (source '("STATUS $== \"paid\" AND TOTAL >= MIN_TOTAL"
                      "ANY(TAGS, _ $== \"gift\") AND CHANNEL $== \"web\""
                      "COUNT(ITEMS) >= 3 AND ALL(ITEMS, I, I[\"qty\"] > 0)"
                      "SUM(ITEMS, I, I[\"qty\"] * I[\"price\"]) != TOTAL"
                      "ANY(ITEMS, I, LEFT(I[\"sku\"], 3) $== \"GM-\")"))
      (multiple-value-bind (rows where rule) (involved conn source)
        (let ((in-memory (remove-if-not
                          (lambda (order) (sel:as-bool (sel:run rule (order-context order items))))
                          (sel:value-values orders))))
          (format t "   ~a~%" source)
          (format t "             sql    ~a~%" (sel.sql:as-condition where))
          (when (sel.sql:bindings where)
            (format t "             params ~{~a~^, ~}~%"
                    (mapcar #'sel:as-text (sel.sql:bindings where))))
          (format t "             rows   ~a | same as in memory: ~a~%"
                  (ids (sel:value-values rows)) (same-p rows in-memory))))))
  0)
