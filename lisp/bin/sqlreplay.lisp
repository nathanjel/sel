;;;; Rebuild the whole shipped map through the public registration API, then
;;;; diff what the translator can observe against the map beside it.
;;;;
;;;;   lisp/bin/sqlreplay
;;;;
;;;; The counterpart of the other hosts' sqlreplay; see js/bin/sqlreplay.mjs for
;;;; why this check exists. sql/MAP.md §4.5¼ is the property it asserts: anything
;;;; the shipped map contains, an application could have registered.

(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defpackage #:sel-sqlreplay (:use #:common-lisp #:sel.sql) (:export #:main))
(in-package #:sel-sqlreplay)

(load (merge-pathnames "map-replay.lisp" (directory-namestring *load-truename*)))

(defparameter +suffix+ "~replay")

(defun same-entry (a b)
  "Compares two entries by MEANING, not by print form: both are plists and the
generator is free to emit their keys in either order."
  (cond ((and (null a) (null b)) t)
        ((or (stringp a) (stringp b)) (equal a b))
        ((or (null a) (null b)) nil)
        (t (flet ((keys (p) (loop for (k nil) on p by #'cddr collect k)))
             (let ((ka (keys a)) (kb (keys b)))
               (and (null (set-difference ka kb)) (null (set-difference kb ka))
                    (every (lambda (k) (equal (getf a k) (getf b k))) ka)))))))

(defun main ()
  (let ((calls (replay-register))
        (problems '())
        (compared 0))
    (dolist (row sel.sql::+dialects+)
      (let* ((name (car row))
             (d (cdr row))
             (twin (concatenate 'string name +suffix+)))
        (incf compared)
        (unless (equal (dialect-version name) (dialect-version twin))
          (push (format nil "~a: version" name) problems))
        (dolist (cell (getf d :lexical))
          (incf compared)
          (unless (equal (dialect-lexical name (car cell))
                         (dialect-lexical twin (car cell)))
            (push (format nil "~a.lexical.~a" name (car cell)) problems)))
        (dolist (section '(:ops :funcs :skel))
          (dolist (cell (getf d section))
            (incf compared)
            (unless (same-entry (dialect-entry name section (car cell))
                                (dialect-entry twin section (car cell)))
              (push (format nil "~a.~(~a~).~a" name section (car cell)) problems))))))
;; Rule 10 of sql/MAP.md, at run time. Registering a derived dialect is the
    ;; documented way to adapt the map to a server -- the first external user of this
    ;; layer did it on their first day -- and numericGuard is the one lexical key where
    ;; a wrong override fails SILENTLY: every other one blows up at template expansion,
    ;; and this one emits SQL that answers where SEL refuses. The generator has
    ;; enforced the rule since the guard existed; nothing enforced it for a dialect the
    ;; generator never sees.
    ;;
    ;; End to end rather than by calling the check directly, because the check being
    ;; right is worth nothing if the emit path does not reach it. The bad guard accepts
    ;; integers only, so it lets through the '2.5' that ISNUM's pattern catches: a
    ;; guard that asks a narrower question passes what it should have stopped.
    (let ((guard-name (concatenate 'string "mariadb" +suffix+ "~guard"))
          (refused nil))
      (define-dialect guard-name
        (list :extends (concatenate 'string "mariadb" +suffix+)
              :lexical (list (cons "numericGuard"
                                   "CASE WHEN ({0} REGEXP '\\\\A-?[0-9]+\\\\z') THEN CAST({0} AS DECIMAL(65,10)) ELSE NULL END"))))
      (handler-case
          (translate (sel:compile-source "T == 25") guard-name
                     (list (cons "T" (binding-column "t" nil :text))))
        (sql-error (e)
          (push (format nil "numericGuard disagreeing with ISNUM raised ~a rather ~
than a registration error" (sql-error-code e)) problems))
        (error () (setf refused t)))
      (unless refused
        (push "a numericGuard that disagrees with its funcs.ISNUM was accepted; ~
sql/MAP.md rule 10 holds at generation time and not at run time" problems)))
    ;; And the memo must not outlive what it vouched for. DEFINE-ENTRY lets the last
    ;; writer win, so an ISNUM registered AFTER a dialect's guard was checked would
    ;; never be compared against it. This dialect inherits a good guard, is translated
    ;; once so the check runs and passes, and then has its ISNUM replaced by one the
    ;; inherited guard does not carry.
    (let* ((memo-name (concatenate 'string "mariadb" +suffix+ "~memo"))
           (program (sel:compile-source "T == 25"))
           (text-col (list (cons "T" (binding-column "t" nil :text))))
           (stale nil))
      (define-dialect memo-name
        (list :extends (concatenate 'string "mariadb" +suffix+)))
      (translate program memo-name text-col)
      (define-entry memo-name :funcs "ISNUM" (list :tpl "({0} REGEXP '^[0-9]+$')" :ret "BOOL"))
      (handler-case (translate program memo-name text-col)
        (sql-error (e)
          (push (format nil "a redefined ISNUM raised ~a rather than a ~
registration error" (sql-error-code e)) problems))
        (error () (setf stale t)))
      (unless stale
        (push "an ISNUM redefined after the guard was checked was not noticed; ~
the memo outlived the pairing it vouched for" problems)))
    (dolist (p (reverse problems)) (format t "  DIFFERS ~a~%" p))
    (format t "~a registration calls rebuilt the map, ~a lookups compared, ~
~a differences (and a disagreeing numericGuard is refused)~%"
            calls compared (length problems))
    (if problems 1 0)))
