;;;; LISP-P2 at the review's size: one sample each of the legal 999,999-digit operations.
;;;;   SEL_LISP_BENCH_ROOT=<tree> sbcl --script tools/perf/lisp/p2-million.lisp
(load (merge-pathnames "harness.lisp" *load-truename*))
(in-package :cl-user)
(defmacro tm (label form)
  `(let ((t0 (get-internal-real-time)) (r nil))
     (setf r (handler-case ,form (sel:sel-error (e) (format nil "!~a" (sel:sel-error-code e)))))
     (format t "~a~c~,1f s~c~a~%" ,label #\Tab (/ (- (get-internal-real-time) t0) internal-time-units-per-second 1.0) #\Tab
             (let ((s (princ-to-string r))) (subseq s 0 (min 24 (length s)))))
     (finish-output)))
(let* ((n 999999) (s (make-string n :initial-element #\7)))
  (tm "dec-parse" (length (princ-to-string (sel::dec-digits (sel::dec-parse s)))))
  (tm "compile+run 'S + 1'" (length (run-src (fmt "~a + 1" s))))
  (tm "compile+run 'S == S'" (run-src (fmt "~a == ~a" s s)))
  (tm "CANON(S)" (length (run-src (fmt "CANON(~a)" s))))
  (tm "S * S (E_RANGE)" (run-src (fmt "~a * ~a" s s)))
  (tm "POWER(9999999999, 99999)" (length (run-src "POWER(9999999999, 99999)")))
  (tm "POWER(3.14159, 100000)" (length (run-src "POWER(3.14159, 100000)"))))
