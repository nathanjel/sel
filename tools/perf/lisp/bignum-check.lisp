;;;; Differential check of KMUL, BIG-POW10 and NUM-DIGITS against the
;;;; built-in implementations. sbcl --script tools/perf/lisp/bignum-check.lisp
(require :asdf)
(load (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname)))
(let ((*standard-output* (make-broadcast-stream)))
  (asdf:load-asd (truename (merge-pathnames "sel-lang.asd" (uiop:ensure-directory-pathname
                                                               (or (uiop:getenv "SEL_LISP_BENCH_ROOT") "lisp/")))))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang))
(in-package :sel)
(defvar *bad* 0)
(defun note (what &rest args) (incf *bad*) (when (< *bad* 12) (format t "MISMATCH ~a ~s~%" what args)))
(let ((st (sb-ext:seed-random-state 7)))
  ;; kmul against *
  (dotimes (i 300)
    (let* ((a (random (ash 1 (+ 100 (random 120000 st))) st)) (b (random (ash 1 (+ 100 (random 120000 st))) st)))
      (unless (= (kmul a b) (* a b)) (note "kmul" (integer-length a) (integer-length b)))))
  ;; big-pow10 against expt
  (dolist (k '(0 1 3999 4000 4001 5000 12345 65536 100000 250001))
    (unless (= (big-pow10 k) (expt 10 k)) (note "big-pow10" k)))
  ;; num-digits against the printed length
  (dotimes (i 200)
    (let ((n (random (ash 1 (+ 64 (random 400000 st))) st)))
      (unless (= (num-digits n) (length (write-to-string n :base 10 :radix nil))) (note "num-digits" (integer-length n))))))
(format t "differences: ~d~%" *bad*)
