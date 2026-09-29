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

(defun tidy-message (message)
  "Call sites write long messages with FORMAT's `~<newline>` continuation, and
REFUSE does not call FORMAT -- so twenty-odd refusals said `~` and a line break in
the middle of a sentence (LISP-C38). Only that directive is processed: a message is
not a control string, and a `~` in a name a user wrote must survive."
  (if (not (search (format nil "~~~%") message))
      message
      (with-output-to-string (out)
        (let ((i 0) (n (length message)))
          (loop while (< i n)
                do (if (and (char= (char message i) #\~) (< (1+ i) n)
                            (char= (char message (1+ i)) #\Newline))
                       (progn (incf i 2)
                              (loop while (and (< i n) (member (char message i) '(#\Space #\Tab)))
                                    do (incf i)))
                       (progn (write-char (char message i) out) (incf i))))))))

(defun refuse (code message &optional pos)
  "Signal at the point of failure. Nothing wraps this on the way out, the same
rule spec/errors.md sets for the evaluator."
  (error 'sql-error :code code :message (tidy-message message)
                    :line (if pos (sel::pos-line pos) 0)
                    :col (if pos (sel::pos-col pos) 0)
                    :offset (if pos (sel::pos-offset pos) 0)))

(defun ascii-digit-p (c)
  "CL's DIGIT-CHAR-P accepts every Unicode decimal digit -- (digit-char-p #\\٣)
is 3 -- and every numeral grammar in this layer is ASCII by specification. The
other hosts get that from a regex character class; this host has to say it.

Without it LIST-KEY answered element 13 for \"1٣\" where Python answers none,
which is the defect _LIST_KEY's own comment describes in a different alphabet."
  (char<= #\0 c #\9))

(defun bad (fmt &rest args)
  "A malformed registration is a mistake in the application's startup, not a rule
the database cannot run, so it is never a SQL-ERROR: TRY-TRANSLATE catches that
and would swallow this."
  (error (apply #'format nil fmt args)))
