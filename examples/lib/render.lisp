;;;; RENDER for the examples — the half of examples/lib/db.lisp that needs no
;;;; database driver.
;;;;
;;;;   (load (merge-pathnames "../lib/render.lisp" *load-truename*))
;;;;   (sel-db:render rows "   | ")
;;;;
;;;; render(rows, pad) prints rows as `field=value` lines. It is written in SEL,
;;;; so it prints the same bytes on every host by construction. In the other four
;;;; hosts it lives in db.*; here it has a file of its own because loading db.lisp
;;;; loads postmodern, cl-sqlite and cl-mysql, and examples/memory-complex renders
;;;; rows without having any of them. db.lisp loads this file, so an example that
;;;; talks to a database loads db.lisp alone.

(defpackage #:sel-db
  (:use #:common-lisp)
  (:export #:render))

(in-package #:sel-db)

(defparameter *render*
  (sel:compile-source
   "JOIN(MAP(ROWS, PAD & JOIN(MAP(_, _K & \"=\" & (_ ?? \"NULL\")), \"  \")), \"\\n\")"))

(defun render (rows &optional (pad ""))
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "ROWS" rows)
    (sel:value-set ctx "PAD" (sel:make-text pad))
    (sel:as-text (sel:run *render* ctx))))
