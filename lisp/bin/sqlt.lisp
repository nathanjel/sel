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

(defun snapshot-ast (n)
  "The tree as a list, with the registry's spec left out: it is looked up by
name and compared by identity, and a snapshot is compared by value. Everything
else that identifies a node -- kind, position, literal, name, operator,
grouping, children -- is in here."
  (cond ((null n) nil)
        ((not (sel::node-p n)) (list :other))
        (t (let ((pos (sel::node-pos n)))
             (list (sel::node-kind n)
                   (and pos (list (sel::pos-line pos) (sel::pos-col pos) (sel::pos-offset pos)))
                   (sel::node-s n) (sel::node-b n) (sel::node-grouped n)
                   (snapshot-ast (sel::node-l n)) (snapshot-ast (sel::node-r n))
                   (mapcar #'snapshot-ast (sel::node-items n)))))))

(defun classify (plan)
  (cond ((hybrid-plan-pure-sql-p plan) "pure_sql")
        ((hybrid-plan-pure-memory-p plan) "pure_memory")
        (t "hybrid")))

(defun run-plan-case (c dialect)
  "NIL when the planner case passes, else a string saying what went wrong.

A `--- plan` case asks the planner rather than the translator. It asserts the
classification, the physical sources, the SQL prefix, that the continuation
exists exactly when the classification says so, and that the caller's AST is
the same tree afterwards -- after planning, which runs stage 1 and the logical
optimiser, and after the physical optimiser RUN uses."
  (let* ((mode (cond ((null (getf c :mode)) :inline)
                     ((equal (getf c :mode) "inline") :inline)
                     ((equal (getf c :mode) "params") :params)
                     ((equal (getf c :mode) "debug") :debug)
                     (t (suite-fail "~a: unknown mode ~a" (getf c :at) (getf c :mode)))))
         (want (getf c :plan))
         (plan nil) (err nil) (program nil) (before nil))
    (handler-case
        (progn
          (when (getf c :register) (funcall (getf c :register)))
          (let ((binds (funcall (getf c :bindings))))
            (setf program (sel:compile-source (getf c :source))
                  before (snapshot-ast (sel:program-ast program))
                  plan (plan-hybrid program dialect binds (list :strict (getf c :strict))))))
      (sql-error (e) (setf err e))
      (sel:sel-error (e)
        (return-from run-plan-case (format nil "the source did not compile: ~a" e)))
      (suite-error (e) (error e))
      (error (e) (suite-fail "~a: unexpected ~a: ~a" (getf c :at) (type-of e) e)))

    (when (equal want "refused")
      (unless err
        (return-from run-plan-case
          (format nil "expected ~a, got a ~a plan" (getf c :error) (classify plan))))
      (destructuring-bind (code line col) (parse-expected (getf c :error) (getf c :at))
        (declare (ignore line col))
        (unless (equal (sql-error-code err) code)
          (return-from run-plan-case
            (format nil "expected ~a, got ~a (~a)" code (sql-error-code err)
                    (sql-error-message err))))
        (return-from run-plan-case nil)))
    (when err
      (return-from run-plan-case
        (format nil "expected a ~a plan, got ~a (~a)" want (sql-error-code err)
                (sql-error-message err))))

    (let ((got (classify plan)))
      (unless (equal got want)
        (return-from run-plan-case (format nil "expected a ~a plan, got ~a" want got))))
    (unless (eq (getf c :tables) :none)
      (unless (equal (hybrid-plan-source-tables plan) (getf c :tables))
        (return-from run-plan-case
          (format nil "source tables got:  ~s~%     want: ~s"
                  (hybrid-plan-source-tables plan) (getf c :tables)))))
    (unless (equal (hybrid-plan-dialect plan) dialect)
      (return-from run-plan-case
        (format nil "plan dialect is ~a, not ~a" (hybrid-plan-dialect plan) dialect)))

    (cond
      ((equal want "pure_memory")
       (when (hybrid-plan-sql-statement plan)
         (return-from run-plan-case "a pure-memory plan carries a SQL statement"))
       (unless (eq (hybrid-plan-continuation-program plan) program)
         (return-from run-plan-case "a pure-memory plan must run the original program"))
       (unless (eq (hybrid-plan-continuation-ast plan) (sel:program-ast program))
         (return-from run-plan-case
           "a pure-memory plan must expose the original AST as its continuation")))
      (t
       (unless (hybrid-plan-sql-statement plan)
         (return-from run-plan-case (format nil "a ~a plan has no SQL statement" want)))
       (unless (hybrid-plan-sql-prefix-ast plan)
         (return-from run-plan-case (format nil "a ~a plan has no SQL prefix AST" want)))
       (let ((sql (as-statement (hybrid-plan-sql-statement plan) mode)))
         (unless (equal sql (getf c :expect))
           (return-from run-plan-case
             (format nil "got:  ~a~%     want: ~a" sql (getf c :expect)))))
       (if (equal want "pure_sql")
           (when (or (hybrid-plan-continuation-program plan)
                     (hybrid-plan-continuation-ast plan))
             (return-from run-plan-case "a pure-SQL plan carries a continuation"))
           (unless (and (hybrid-plan-continuation-program plan)
                        (hybrid-plan-continuation-ast plan)
                        (hybrid-plan-hybrid-p plan))
             (return-from run-plan-case "a hybrid plan has no continuation")))))

    ;; Planning must not have touched the tree, and neither may the physical
    ;; optimiser that every RUN goes through.
    (unless (equal (snapshot-ast (sel:program-ast program)) before)
      (return-from run-plan-case "planning mutated the program AST"))
    (sel:optimize-ast-in-memory (sel:program-ast program))
    (unless (equal (snapshot-ast (sel:program-ast program)) before)
      (return-from run-plan-case "the physical optimiser mutated the program AST"))
    nil))

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
         (sql nil) (err nil) (thrown nil) (frag nil) (program nil) (binds nil))
    (handler-case
        (progn
          ;; Inside the handler: a bad registration is one of the outcomes a case
          ;; may assert, so it has to be catchable rather than fatal.
          (when (getf c :register) (funcall (getf c :register)))
          ;; Inside it too: a binding constructor refuses a malformed binding at
          ;; construction, and that refusal is one of the outcomes a case asserts.
          (setf binds (funcall (getf c :bindings)))
          (setf program (sel:compile-source (getf c :source)))
          (progn
            (setf frag (translate program dialect binds
                                  (list :strict (getf c :strict))))
            (setf sql (cond ((equal as "condition") (as-condition frag mode))
                            ((equal as "statement") (as-statement frag mode))
                            (t (as-value frag mode))))))
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

    ;; Every `--- as statement` case is also run through translate-statement,
    ;; the public full-delegation entry point, which must say exactly what
    ;; TRANSLATE says -- the same text, or the same refusal at the same column.
    ;; Two hosts ran the logical optimiser in that lane and three did not, and
    ;; only a twin check can see it (review 2026-09-15 finding C).
    (when (and (equal as "statement") program)
      (let ((twin-sql nil) (twin-err nil))
        (handler-case
            (setf twin-sql (as-statement (translate-statement program dialect binds
                                                              (list :strict (getf c :strict)))
                                         mode))
          (sql-error (e) (setf twin-err e))
          (suite-error (e) (error e))
          (error (e) (suite-fail "~a: translate-statement signalled ~a: ~a" (getf c :at) (type-of e) e)))
        (flet ((got (e) (if e (format nil "~a at ~a:~a" (sql-error-code e) (sql-error-line e) (sql-error-col e)) "SQL")))
          (cond
            ((or err twin-err)
             (unless (and err twin-err
                          (equal (sql-error-code err) (sql-error-code twin-err))
                          (= (sql-error-line err) (sql-error-line twin-err))
                          (= (sql-error-col err) (sql-error-col twin-err)))
               (return-from run-case
                 (format nil "translate() gave ~a but translate-statement gave ~a" (got err) (got twin-err)))))
            ((not (equal twin-sql sql))
             (return-from run-case
               (format nil "translate-statement disagrees with translate():~%     ~a~%     ~a" twin-sql sql)))))))

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
    (let ((debug-render (cond ((equal as "statement") (as-statement frag :debug))
                              ((equal as "condition") (as-condition frag :debug))
                              (t (as-value frag :debug)))))
      (unless (= (length (bindings frag)) (count-slots debug-render))
        (return-from run-case "bindings and the emitted placeholders disagree in count")))

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
                                          (if (getf c :plan)
                                              (run-plan-case c (getf c :dialect))
                                              (run-case c (getf c :dialect))))
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
                                       (if (getf c :plan)
                                           (run-plan-case c m)
                                           (run-case c m)))
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
