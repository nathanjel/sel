;;;; A database runner for the SQL examples — Common Lisp.
;;;;
;;;;   (load (merge-pathnames "../lib/db.lisp" *load-truename*))
;;;;   (sel-db:connect "postgresql")  (sel-db:query conn sql params)
;;;;   (sel-db:runner conn)           (sel-db:render rows pad)
;;;;
;;;; Every example that talks to a database goes through the functions below,
;;;; and the four files beside this one do the same with their own drivers. The
;;;; contract is small on purpose, because it is what makes five hosts print the
;;;; same thing:
;;;;
;;;;   - CONNECT opens PostgreSQL, MariaDB or SQLite from SEL_DB_* in the
;;;;     environment (tools/check-usage.sh sets them).
;;;;   - QUERY runs a statement whose placeholders are `?` — the spelling SEL's
;;;;     :PARAMS mode emits — and returns the rows as a SEL value: a list of
;;;;     records, every column TEXT and SQL NULL as NULL. Money stays text, as it
;;;;     does everywhere in SEL; a float reaching here is an error.
;;;;   - RUNNER is QUERY in the shape SEL.SQL:EXECUTE-HYBRID wants.
;;;;   - RENDER prints rows as `field=value` lines. It is written in SEL, so it
;;;;     prints the same bytes on every host by construction. It lives in
;;;;     render.lisp, which this file loads: examples/memory-complex renders rows
;;;;     without loading any driver.
;;;;
;;;; Three drivers, three ways to be handed parameters. cl-sqlite takes `?` as it
;;;; is. cl-postgres (postmodern's protocol layer) wants $1, $2, ... cl-mysql
;;;; has no parameters at all, so the values are escaped by the server's own
;;;; mysql_real_escape_string and written into the statement as quoted literals,
;;;; as PDO's emulated prepares do. PLACEHOLDERS finds the `?`s: one outside a
;;;; quoted literal or identifier is a placeholder (a numeric guard's regex
;;;; literal has `?` in it, which is why this cannot be a plain replace).
;;;;
;;;; Each driver also has an opinion about types, and every one is overruled:
;;;; postmodern would read NUMERIC as a ratio (12.50 and 25/2 are one number and
;;;; two SEL values), so those types are read as text; cl-mysql converts nothing
;;;; once no type map is given; cl-sqlite's integers are printed in decimal.

;;; The drivers first, because --load reads and evaluates one top-level form at a
;;; time: the SQLITE, CL-POSTGRES and CL-MYSQL symbols below are only readable
;;; once those packages exist.
(let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
  (funcall (find-symbol "QUICKLOAD" "QL") '(:postmodern :sqlite :cl-mysql)))
(load (merge-pathnames "render.lisp" *load-truename*))

(in-package #:sel-db)

(export '(connect query runner))

(defun env (name &optional default)
  (or (uiop:getenv name) default))

;; EXAMPLE-BEGIN runner
(defun connect (dialect)
  (let ((host (env "SEL_DB_HOST" "127.0.0.1"))
        (user (env "SEL_DB_USER"))
        (password (env "SEL_DB_PASSWORD"))
        (database (env "SEL_DB_NAME")))
    (cond ((string= dialect "sqlite")
           (sqlite:connect (env "SEL_DB_SQLITE_FILE")))
          ((string= dialect "postgresql")
           (postmodern:connect database user password host
                               :port (parse-integer (env "SEL_DB_POSTGRESQL_PORT"))))
          ((string= dialect "mariadb")
           (cl-mysql:connect :host host :user user :password password :database database
                             :port (parse-integer (env "SEL_DB_MARIADB_PORT"))))
          (t (error "no runner for ~a" dialect)))))

(defun placeholders (sql replacement backslash-escapes)
  "SQL with its Nth placeholder, counting from 0, replaced by (REPLACEMENT N)."
  (with-output-to-string (out)
    (let ((in-quote nil) (n 0) (i 0))
      (loop while (< i (length sql))
            do (let ((c (char sql i)))
                 (cond (in-quote
                        (cond ((and (char= c #\\) (char= in-quote #\') backslash-escapes
                                    (< (1+ i) (length sql)))
                               (write-char c out)
                               (setf c (char sql (incf i))))
                              ((char= c in-quote)
                               (setf in-quote nil)))
                        (write-char c out))
                       ((find c "'\"`")
                        (setf in-quote c)
                        (write-char c out))
                       ((char= c #\?)
                        (write-string (funcall replacement n) out)
                        (incf n))
                       (t (write-char c out)))
                 (incf i))))))

(defun param-text (p)
  "A bound SEL value as the driver takes it: its text, or NIL for NULL."
  (unless (sel:value-null-p p) (sel:as-text p)))

(defgeneric fetch (conn sql params)
  (:documentation "The rows of SQL, each an alist of column name and cell, where
a cell is a string, an integer or :NULL."))

(defmethod fetch ((db sqlite:sqlite-handle) sql params)
  (let ((statement (sqlite:prepare-statement db sql)))   ; `?` is SQLite's own
    (unwind-protect
         (let ((names (sqlite:statement-column-names statement)))
           (loop for p in params
                 for i from 1
                 do (sqlite:bind-parameter statement i (param-text p)))
           (loop while (sqlite:step-statement statement)
                 collect (loop for name in names
                               for i from 0
                               collect (cons name (or (sqlite:statement-column-value statement i)
                                                      :null)))))
      (sqlite:finalize-statement statement))))

(defparameter *pg-readtable*
  (let ((table (cl-postgres:copy-sql-readtable)))
    (dolist (oid (list cl-postgres-oid:+int2+ cl-postgres-oid:+int4+ cl-postgres-oid:+int8+
                       cl-postgres-oid:+numeric+ cl-postgres-oid:+date+))
      (cl-postgres:set-sql-reader oid nil :table table))   ; NIL: read it as text
    table)
  "cl-postgres's readers, less the ones that would turn a number or a date into
something other than the text PostgreSQL printed.")

(defmethod fetch ((conn cl-postgres:database-connection) sql params)
  (let ((cl-postgres:*sql-readtable* *pg-readtable*))
    (cl-postgres:prepare-query conn "" (placeholders sql (lambda (n) (format nil "$~D" (1+ n))) nil))
    (cl-postgres:exec-prepared conn "" (mapcar (lambda (p) (or (param-text p) :null)) params)
                               'cl-postgres:alist-row-reader)))

;;; cl-mysql reads a zero-length cell as NIL, so '' and NULL would come back
;;; alike. The rows are read here instead, where a NULL cell is a null pointer.
(cffi:defcfun ("mysql_fetch_row" mysql-fetch-row) :pointer (result :pointer))
(cffi:defcfun ("mysql_fetch_lengths" mysql-fetch-lengths) :pointer (result :pointer))

(defun mysql-literal (pool p)
  (if (sel:value-null-p p)
      "NULL"
      (format nil "'~a'" (cl-mysql:escape-string (sel:as-text p) :database pool))))

(defmethod fetch ((pool cl-mysql-system:connection-pool) sql params)
  (let ((conn (cl-mysql:query (placeholders sql (lambda (n) (mysql-literal pool (nth n params))) t)
                              :database pool :store nil)))
    (unwind-protect
         (progn
           (cl-mysql:next-result-set conn :store t :dont-release t)
           (let ((result (cl-mysql-system:result-set conn))
                 (fields (first (cl-mysql:result-set-fields conn))))   ; (name type flags)
             (loop for (name type) in fields
                   when (member type '(:float :double))
                     do (error "a float reached SEL (~a); declare the column DECIMAL or TEXT" name))
             (loop for row = (mysql-fetch-row result)
                   until (cffi:null-pointer-p row)
                   collect (loop with lengths = (mysql-fetch-lengths result)
                                 for (name) in fields
                                 for i from 0
                                 for cell = (cffi:mem-aref row :pointer i)
                                 collect (cons name
                                               (if (cffi:null-pointer-p cell)
                                                   :null
                                                   (cffi:foreign-string-to-lisp
                                                    cell :count (cffi:mem-aref lengths :unsigned-long i)
                                                         :encoding :utf-8)))))))
      (cl-mysql-system:release conn))))

(defun cell-text (cell)
  (etypecase cell
    (string cell)
    (integer (format nil "~D" cell))
    (float (error "a float reached SEL; declare the column DECIMAL or TEXT"))))

(defun query (conn sql &optional params)
  (let ((rows (sel:make-none)))
    (loop for row in (fetch conn sql params)
          for n from 1
          do (let ((record (sel:make-none)))
               (loop for (name . cell) in row
                     do (sel:value-set record name (if (eq cell :null)
                                                       (sel:make-null)
                                                       (sel:make-text (cell-text cell)))))
               (sel:value-set rows (format nil "~D" n) record)))
    rows))

(defun runner (conn)
  (lambda (sql params) (query conn sql params)))
;; EXAMPLE-END runner
