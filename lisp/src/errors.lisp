;;;; Errors.
;;;;
;;;; An error is raised at the innermost point of failure and propagates
;;;; unchanged. No layer wraps it, prefixes it, or attaches a stack trace.
;;;; `code` is a stable identifier from spec/errors.md and part of the language's
;;;; contract; the message is human text, free to change and to be translated.
;;;; Conformance tests assert on the code and the position only.

(in-package #:sel)

;;; A source position, 1-based in code points. NIL line means "no position",
;;; used for failures raised from host code rather than from a node.
(defstruct (pos (:constructor make-pos (line col offset)))
  (line 0 :type fixnum)
  (col 0 :type fixnum)
  (offset 0 :type fixnum))

(define-condition sel-error (error)
  ((code :initarg :code :reader sel-error-code)
   (message :initarg :message :reader sel-error-message)
   (line :initarg :line :initform 0 :reader sel-error-line)
   (col :initarg :col :initform 0 :reader sel-error-col)
   (offset :initarg :offset :initform 0 :reader sel-error-offset))
  (:report (lambda (c stream)
             (format stream "~a at ~d:~d: ~a"
                     (sel-error-code c) (sel-error-line c) (sel-error-col c)
                     (sel-error-message c)))))

(defun fail (code message &optional at)
  "Raise CODE at AT, which is a POS or NIL."
  (error 'sel-error
         :code code
         :message message
         :line (if at (pos-line at) 0)
         :col (if at (pos-col at) 0)
         :offset (if at (pos-offset at) 0)))
