;;;; The SEL->SQL conformance suite for the Common Lisp host.
;;;;
;;;;   lisp/bin/sqlt                 every case
;;;;   lisp/bin/sqlt bind. agg.      only cases whose name contains one of these
;;;;   lisp/bin/sqlt --names         what this host loaded, and stop
;;;;   lisp/bin/sqlt --print-base 16 every case, with the printer set hostile
;;;;
;;;; --print-base is this host's alone and pins something no case file can
;;;; state. *PRINT-BASE* belongs to the calling application, and this layer may
;;;; not read it: every number it decides at translation time is a SEL number,
;;;; and SEL numbers are decimal by specification (spec/SPEC.md 4). A single
;;;; PRINC-TO-STRING anywhere in the translator renders a twelve-element list's
;;;; count as "C" under a caller that had rebound it, MAKE-NUM raises E_NOT_NUM,
;;;; and that SEL-ERROR escapes TRY-TRANSLATE, which catches SQL-ERROR alone.
;;;; Running the whole corpus this way says so for every such site at once, and
;;;; keeps saying it for sites not written yet.
;;;;
;;;; The cases live in sql/cases/*.sqlt and reach here through
;;;; tools/gen-sql-cases.mjs, which is the only thing that reads them. Nothing
;;;; in this file parses anything: a case's bindings and registrations arrive as
;;;; constructor calls.

;;; boot.lisp brings in :sel-lang; the SQL layer is its own system, for the
;;; reason the JS host keeps its translator behind a separate entry point.
(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-sqlt
  (:use #:common-lisp #:sel.sql)
  (:export #:main))

(in-package #:sel-sqlt)

(defvar *corpus-print-base* 10
  "What *PRINT-BASE* is bound to while a case translates -- see --print-base.
Bound around the translation alone and not around this file's own reporting, so
the counts in the summary stay readable whatever it is set to.")

(load (merge-pathnames "case-data.lisp" (directory-namestring *load-truename*)))

;;; `--- throws` names PHP's class, because the cases were written for one host.
;;; The line it draws is the one that matters and it is the same everywhere: a
;;; malformed map or registration is a mistake in the application's startup, not
;;; a rule that cannot be translated, so it must NOT be catchable as SQL-ERROR.
;;; A name with no mapping is a suite error and never a pass.
(defun throws-known-p (name) (equal name "LogicException"))

(defparameter +mirrors+ '(("mariadb" . "mysql")))

(define-condition suite-error (error)
  ((text :initarg :text :reader suite-error-text))
  (:report (lambda (c s) (write-string (suite-error-text c) s))))

(defun suite-fail (fmt &rest args)
  (error 'suite-error :text (apply #'format nil fmt args)))

(defun parse-expected (s at)
  "\"CODE\" or \"CODE line:col\"."
  (let ((sp (position #\Space s)))
    (if (null sp)
        (list s nil nil)
        (let* ((rest (string-left-trim " " (subseq s (1+ sp))))
               (colon (position #\: rest)))
          (unless colon (suite-fail "~a: malformed error expectation" at))
          (list (subseq s 0 sp)
                (parse-integer rest :end colon)
                (parse-integer rest :start (1+ colon)))))))

(defun count-slots (s)
  "`~1~`, not bare tildes: PostgreSQL's regex operator IS `~`, so counting those
divided a regex fragment's odd tilde count by two."
  (let ((n 0) (i 0) (len (length s)))
    (loop while (< i len)
          do (if (char= (char s i) #\~)
                 (let ((j (1+ i)))
                   (loop while (and (< j len) (digit-char-p (char s j))) do (incf j))
                   (if (and (> j (1+ i)) (< j len) (char= (char s j) #\~))
                       (progn (incf n) (setf i (1+ j)))
                       (incf i)))
                 (incf i)))
    n))

(defun run-case (c dialect)
  "NIL when the case passes, else a string saying what went wrong."
  (when (or (null dialect) (equal dialect ""))
    (suite-fail "~a: case ~a has no --- dialect" (getf c :at) (getf c :name)))
  (let* ((as (or (getf c :as) "value"))
         (mode (cond ((null (getf c :mode)) :inline)
                     ((equal (getf c :mode) "inline") :inline)
                     ((equal (getf c :mode) "params") :params)
                     ((equal (getf c :mode) "debug") :debug)
                     (t (suite-fail "~a: unknown mode ~a" (getf c :at) (getf c :mode)))))
         (sql nil) (err nil) (thrown nil) (frag nil))
    (handler-case
        (progn
          ;; Inside the handler: a bad registration is one of the outcomes a case
          ;; may assert, so it has to be catchable rather than fatal.
          (when (getf c :register) (funcall (getf c :register)))
          ;; Inside it too: a binding constructor refuses a malformed binding at
          ;; construction, and that refusal is one of the outcomes a case asserts.
          (let ((binds (funcall (getf c :bindings))))
            (setf frag (translate (sel:compile-source (getf c :source)) dialect binds
                                  (list :strict (getf c :strict))))
            (setf sql (if (equal as "condition") (as-condition frag mode) (as-value frag mode)))))
      (sql-error (e) (setf err e))
      (sel:sel-error (e)
        (return-from run-case (format nil "the source did not compile: ~a" e)))
      (suite-error (e) (error e))
      (error (e) (setf thrown e)))

    (when (getf c :throws)
      (unless (throws-known-p (getf c :throws))
        (suite-fail "~a: no Lisp equivalent is recorded for --- throws ~a"
                    (getf c :at) (getf c :throws)))
      (return-from run-case
        (if thrown nil (format nil "expected ~a, got ~a" (getf c :throws)
                               (if err (sql-error-code err) sql)))))
    (when thrown
      (suite-fail "~a: unexpected ~a: ~a" (getf c :at) (type-of thrown) thrown))

    (when (getf c :error)
      (unless err
        (return-from run-case (format nil "expected ~a, got ~s" (getf c :error) sql)))
      (destructuring-bind (code line col) (parse-expected (getf c :error) (getf c :at))
        (unless (equal (sql-error-code err) code)
          (return-from run-case
            (format nil "expected ~a, got ~a (~a)" code (sql-error-code err)
                    (sql-error-message err))))
        (when line
          (unless (and (= line (sql-error-line err)) (= col (sql-error-col err)))
            (return-from run-case
              (format nil "expected ~a at ~a:~a, got it at ~a:~a" code line col
                      (sql-error-line err) (sql-error-col err)))))
        (return-from run-case nil)))

    (when err
      (return-from run-case (format nil "expected SQL, got ~a (~a)"
                                    (sql-error-code err) (sql-error-message err))))
    (unless (equal sql (or (getf c :expect) ""))
      (return-from run-case (format nil "got:  ~a~%     want: ~a" sql (getf c :expect))))

    ;; Checked for every case that produces a fragment, not only those asking
    ;; about params: every slot in the part list must have a value, and every
    ;; value must be emitted. A value bound but never emitted means a fragment
    ;; was rendered and thrown away -- invisible in inline mode.
    (let ((seen '()) (n (length (fragment-params frag))))
      (dolist (p (fragment-parts frag))
        (when (integerp p)
          (when (or (< p 1) (> p n))
            (return-from run-case (format nil "parameter slot ~a has no value in params" p)))
          (pushnew p seen)))
      (let ((orphans (loop for i from 1 to n unless (member i seen) collect i)))
        (when orphans
          (return-from run-case
            (format nil "parameter slot(s) ~a were bound but never emitted — a ~
fragment was rendered and discarded" orphans)))))
    (unless (= (length (bindings frag)) (count-slots (as-value frag :debug)))
      (return-from run-case "bindings and the emitted placeholders disagree in count"))

    (when (getf c :params)
      (let ((got (format nil "~{~a~^, ~}" (mapcar #'sel:value-dump (bindings frag)))))
        (unless (equal got (getf c :params))
          (return-from run-case (format nil "params got:  ~a~%     want: ~a"
                                        got (getf c :params))))))
    nil))

(defun main ()
  (let* ((raw (sel-cli:script-args))
         (bp (position "--print-base" raw :test #'string=))
         (*corpus-print-base* (if bp (parse-integer (nth (1+ bp) raw)) 10))
         (args (if bp (append (subseq raw 0 bp) (subseq raw (+ bp 2))) raw))
         (filters (remove-if (lambda (a) (string= a "--names")) args))
         (passed 0) (mirrored 0) (suite-errors 0) (failures '()))
    (when (member "--names" args :test #'string=)
      (dolist (c +sql-cases+) (format t "~a	~a~%" (getf c :at) (getf c :name)))
      (return-from main 0))
    (dolist (c +sql-cases+)
      (when (or (null filters)
                (some (lambda (f) (search f (getf c :name))) filters))
        (map-reset)                       ; no case may leak a registration
        (let ((problem (handler-case (let ((*print-base* *corpus-print-base*))
                                          (run-case c (getf c :dialect)))
                         (suite-error (e)
                           (format t "SUITE ERROR ~a~%" e) (incf suite-errors) :skip))))
          (cond
            ((eq problem :skip))
            (problem (push (cons c problem) failures))
            (t (incf passed)
               ;; The same case under the mirrored dialect. A registration case
               ;; is exempt: it names its dialect in the register data.
               (let ((m (cdr (assoc (getf c :dialect) +mirrors+ :test #'equal))))
                 (when (and m (null (getf c :register)))
                   (map-reset)
                   (let ((p2 (handler-case (let ((*print-base* *corpus-print-base*))
                                       (run-case c m))
                               (suite-error (e)
                                 (format t "SUITE ERROR (mirrored to ~a) ~a~%" m e)
                                 (incf suite-errors) :skip))))
                     (cond ((eq p2 :skip))
                           (p2 (push (cons c (format nil "mirrored to ~a, which must ~
agree with ~a: ~a" m (getf c :dialect) p2)) failures))
                           (t (incf mirrored)))))))))))
    (dolist (f (reverse failures))
      (format t "FAIL ~a  (~a)~%     ~a~%" (getf (car f) :name) (getf (car f) :at) (cdr f)))
    (format t "~%~a passed (~a also checked against a mirrored dialect), ~a failed, ~
~a suite errors~%" passed mirrored (length failures) suite-errors)
    (if (and (null failures) (zerop suite-errors)) 0 1)))
