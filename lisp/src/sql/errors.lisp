;;;; Translator errors. See sql/errors.md -- codes are contract, messages are not.
;;;;
;;;; Unlike SEL-ERROR the message here is expected to be READ: a translator error
;;;; is not a bug report, it is an answer. *This rule cannot be pushed into this
;;;; database, and here is what stopped it.*

(in-package #:sel.sql)

(define-condition sql-error (error)
  ((code :initarg :code :reader sql-error-code)
   (message :initarg :message :reader sql-error-message)
   (line :initarg :line :initform 0 :reader sql-error-line)
   (col :initarg :col :initform 0 :reader sql-error-col)
   (offset :initarg :offset :initform 0 :reader sql-error-offset))
  (:report (lambda (c s)
             (format s "~a at ~a:~a: ~a" (sql-error-code c) (sql-error-line c)
                     (sql-error-col c) (sql-error-message c))))
  (:documentation "One class, one message. No diagnostics object, no EXPLAIN."))

(defun refuse (code message &optional pos)
  "Signal at the point of failure. Nothing wraps this on the way out, the same
rule spec/errors.md sets for the evaluator."
  (error 'sql-error :code code :message message
                    :line (if pos (sel::pos-line pos) 0)
                    :col (if pos (sel::pos-col pos) 0)
                    :offset (if pos (sel::pos-offset pos) 0)))

(defun bad (fmt &rest args)
  "A malformed registration is a mistake in the application's startup, not a rule
the database cannot run, so it is never a SQL-ERROR: TRY-TRANSLATE catches that
and would swallow this."
  (error (apply #'format nil fmt args)))
