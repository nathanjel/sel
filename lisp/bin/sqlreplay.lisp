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
    (dolist (p (reverse problems)) (format t "  DIFFERS ~a~%" p))
    (format t "~a registration calls rebuilt the map, ~a lookups compared, ~
~a differences~%" calls compared (length problems))
    (if problems 1 0)))
