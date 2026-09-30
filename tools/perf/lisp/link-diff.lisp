;;;; Differential for the LINK `equality AND residual` path (LISP-P9).
;;;;   SEL_LISP_BENCH_ROOT=<tree> sbcl --script tools/perf/lisp/link-diff.lisp > out.tsv
;;;; Run against a baseline tree and the working tree and diff the two files: every
;;;; program (random rows, including texts that are not numbers, NULLs, repeated and
;;;; missing keys, residuals that raise) must print the same value or the same error
;;;; and position.
(load (merge-pathnames "harness.lisp" *load-truename*))
(in-package :cl-user)
(defvar *st* (sb-ext:seed-random-state 20260930))
(defun pick (&rest xs) (nth (random (length xs) *st*) xs))
(defun rnd-val ()
  (case (random 12 *st*)
    (0 nil) (1 "x") (2 "1.50") (3 "-0") (4 "abc") (t (random 5 *st*))))
(defun rnd-row ()
  (let ((r '()))
    (push (cons "k" (if (zerop (random 14 *st*)) (rnd-val) (random 4 *st*))) r)
    (push (cons "v" (rnd-val)) r)
    (when (plusp (random 6 *st*)) (push (cons "w" (random 7 *st*)) r))
    (nreverse r)))
(defun rows (n) (loop repeat n collect (rnd-row)))
(defparameter *lead* '("l[\"k\"] == r[\"k\"]" "r[\"k\"] == l[\"k\"]" "l[\"k\"] $== r[\"k\"]" "ABS(l[\"k\"]) == r[\"k\"]"))
(defparameter *resid*
  '("l[\"v\"] < r[\"v\"]" "l[\"w\"] != r[\"w\"]" "r[\"w\"] > 2" "TRUE" "FALSE" "l[\"v\"] == 1"
    "(l[\"w\"] + r[\"w\"]) > 3" "l[\"v\"] $== r[\"v\"]" "LEN(l[\"v\"]) > 0" "l[\"w\"] ?? 0 > 1"
    "NOT (l[\"w\"] == r[\"w\"])" "l[\"v\"] == r[\"v\"]" "1 / (l[\"w\"] - r[\"w\"]) > 0"))
(defun rnd-pred ()
  (let ((p (apply #'pick *lead*)))
    (dotimes (i (1+ (random 3 *st*)))
      (setf p (format nil "~a AND ~a" p (apply #'pick *resid*))))
    p))
(dotimes (id 4000)
  (let* ((f (pick "LINK" "LINK_LEFT"))
         (src (format nil "COUNT(~a(L, R, l, r, ~a))" f (rnd-pred)))
         (src2 (format nil "~a(L, R, l, r, ~a) .> MAP(_[\"l\"][\"k\"] & \"/\" & _[\"r\"][\"v\"]) .> JOIN(\",\")"
                       f (rnd-pred)))
         (ctx (sel:from-native (list (cons "L" (rows (random 7 *st*))) (cons "R" (rows (random 7 *st*)))))))
    (dolist (s (list src src2))
      (format t "~d	~a	~a~%" id s
              (handler-case (sel:value-dump (sel:run (sel:compile-source s) ctx))
                (sel:sel-error (e) (format nil "!~a@~a:~a" (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)))
                (error (e) (format nil "!HOST ~a" (type-of e))))))))
