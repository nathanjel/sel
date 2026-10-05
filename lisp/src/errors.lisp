;;;; Errors.
;;;;
;;;; An error is raised at the innermost point of failure and propagates
;;;; unchanged. No layer wraps it, prefixes it, or attaches a stack trace.
;;;; `code` is a stable identifier from spec/errors.md and part of the language's
;;;; contract; the message is human text, free to change and to be translated.
;;;; Conformance tests assert on the code and the position only.

(in-package #:sel)

;;; A source position, 1-based in code points. Line 0 (FAIL's default when it is
;;; given no position) means "no position": a failure raised from host code
;;; rather than from a node.
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

(defun quote-text (s)
  "S as a message quotes it (spec/errors.md, \"Message conventions\"): a JSON
string literal with every non-ASCII code point as itself. ~S is not that -- it
leaves control characters raw."
  (with-output-to-string (out)
    (write-char #\" out)
    (loop for c across s
          for k = (char-code c)
          do (case k
               (34 (write-string "\\\"" out))
               (92 (write-string "\\\\" out))
               (10 (write-string "\\n" out))
               (13 (write-string "\\r" out))
               (9 (write-string "\\t" out))
               (8 (write-string "\\b" out))
               (12 (write-string "\\f" out))
               (t (if (< k 32)
                      (format out "\\u~(~4,'0x~)" k)
                      (write-char c out)))))
    (write-char #\" out)))

(defun describe-char (c)
  "One source character as the lexer's refusal names it: quoted, and, outside
printable ASCII, with its code point, so an invisible one shows."
  (let ((k (char-code c)))
    (if (<= #x21 k #x7e)
        (quote-text (string c))
        (format nil "~a (U+~4,'0X)" (quote-text (string c)) k))))

;;; spec/SPEC.md §6.4's three caps, which are one number. The parser's nesting,
;;; the evaluator's, and a value's -- each is a recursion over a structure the
;;; input can grow without bound, and each finds this host's own control stack
;;; instead of an error if it is not counted. It lives here, with FAIL, because
;;; this file loads before every other and none loads before it, and because the
;;; number and the E_DEPTH it raises are the same fact.
(defconstant +max-depth+ +limit-max-depth+)   ; spec/limits.json, checked against the spec text


;;; The size caps of SPEC 6.4. A result that would be longer than MAX_TEXT_LEN
;;; (code points of TEXT, bytes of BIN) or hold more than MAX_COLLECTION children
;;; is E_RANGE at the node that builds it, decided from the length the result
;;; WOULD have, before anything is allocated: a host that allocates first dies on
;;; the request it was meant to refuse. An empty result is never too large, and
;;; callers arrange that a count that merely clamps never reaches these.
(defun check-text-cap (len pos)
  (when (> len +limit-max-text-len+)
    (fail "E_RANGE"
          (format nil "the result would be longer than ~d" +limit-max-text-len+)
          pos)))

(defun check-collection-cap (n pos)
  (when (> n +limit-max-collection+)
    (fail "E_RANGE"
          (format nil "the result would have more than ~d elements" +limit-max-collection+)
          pos)))
