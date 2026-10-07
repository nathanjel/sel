;;;; A ray tracer in SEL, and the host function it needs -- from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/raytrace/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;   ... --eval '(sel-example:main)' --end-toplevel-options --ppm 640 360 2 > mark.ppm
;;;;   ... --eval '(sel-example:main)' --end-toplevel-options --bench report.json
;;;;
;;;; raytrace.sel draws the SEL mark in glass. SEL has no square root -- a square
;;;; root has no exact decimal result -- so the application gives it one: SQRT(x, n)
;;;; is the square root of x to n fractional digits (10 if n is left out). Like
;;;; `/`, it is exact when it can be: a root with at most n fractional digits comes
;;;; back at its minimal scale, and any other is rounded half away from zero to
;;;; exactly n. It is computed on whole numbers, so every host gives every digit
;;;; the same. For x = m / 10^s, x has an exact root when m, with the scale made
;;;; even, is a perfect square -- which costs what x's size costs, whatever n is.
;;;; Any other root is rounded from
;;;;
;;;;     sqrt(x) * 10^n = sqrt(m * 10^(2n - s))
;;;;
;;;; whose integer square root is the truncated answer; one comparison of whole
;;;; numbers decides the rounding.
;;;;
;;;; With no arguments it prints a few square roots and a small frame; --ppm prints
;;;; a frame of any size as PPM, and --bench times the frame the benchmarks use.
;;;; The files beside this one print byte-identical output.

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: raytrace.sel sits beside it.")

(defconstant +max-scale+ 1000000 "The cap ROUND's scale has (spec/limits.json).")

;; EXAMPLE-BEGIN sqrt
(defun square-root (args)
  ;; AS-DEC on the argument's value and position is the decimal reader the
  ;; builtins use: E_NOT_NUM, E_NULL, ... at x's position.
  (let* ((x (sel:as-dec (sel:args-val args 0) (sel:args-pos-of args 0)))
         (n (if (> (sel:args-count args) 1) (sel:args-non-neg-int args 1) 10)))
    (when (> n +max-scale+)
      (sel:fail "E_RANGE" "SQRT: scale above 1000000" (sel:args-pos-of args 1)))
    (when (sel:dec-neg x)
      (sel:fail "E_RANGE" "SQRT of a negative number" (sel:args-pos-of args 0)))
    (let ((m (sel:dec-digits x))
          (s (sel:dec-scale x)))
      ;; An exact root is read off x itself, so its cost depends on x, not n: with
      ;; the scale made even, sqrt(m / 10^s) is r / 10^(s/2) when m is r^2.
      (multiple-value-bind (m2 s2) (if (evenp s) (values m s) (values (* 10 m) (1+ s)))
        (let ((r (isqrt m2))
              (scale (/ s2 2)))
          (when (= (* r r) m2)
            ;; drop the zeros it does not need, in halving chunks -- log t
            ;; divisions for a root with t of them, not t
            (let ((step 1))
              (loop while (<= (* 2 step) scale) do (setf step (* 2 step)))
              (loop while (and (> step 0) (> scale 0))
                    do (when (<= step scale)
                         (multiple-value-bind (q rem) (floor r (expt 10 step))
                           (when (zerop rem)
                             (setf r q)
                             (decf scale step))))
                       (setf step (floor step 2))))
            (when (<= scale n)        ; else more digits than n allows: round it below
              (return-from square-root (sel:make-num (sel:dec-make nil r scale)))))))
      ;; Not exact at scale n, so rounded there. sqrt(x) * 10^n = sqrt(m * 10^e)
      ;; with e = 2n - s; when e is negative that is sqrt(m / 10^-e), whose
      ;; integer part is isqrt(m div 10^-e)
      (let* ((e (- (* 2 n) s))
             (v (if (>= e 0) (* m (expt 10 e)) m))
             (p (if (>= e 0) 1 (expt 10 (- e))))
             (r (isqrt (floor v p))))
        (when (>= (* 4 v) (* (expt (1+ (* 2 r)) 2) p)) ; at or past the half: away from zero
          (incf r))
        (sel:make-num (sel:dec-make nil r n))))))

(sel:register-function "SQRT" 1 2 #'square-root)
;; EXAMPLE-END sqrt

(defun read-here (name)
  (uiop:read-file-string (merge-pathnames name *here*) :external-format :utf-8))

(defun frame (scene w h ss)
  (sel:as-text (sel:run scene (sel:from-native (list (cons "W" (princ-to-string w))
                                                     (cons "H" (princ-to-string h))
                                                     (cons "SS" (princ-to-string ss)))))))

(defun arguments ()
  "The words after --end-toplevel-options. SBCL drops the marker from
*posix-argv* on some versions and keeps it on others."
  (let ((argv sb-ext:*posix-argv*))
    (rest (or (member "--end-toplevel-options" argv :test #'string=) argv))))

(declaim (ftype function bench))        ; below MAIN, as in the files beside this one

(defun main (&optional (argv (arguments)))
  (let ((scene (sel:compile-source (read-here "raytrace.sel"))))
    (when (and (equal (first argv) "--ppm") (= (length argv) 4))
      (write-string (apply #'frame scene (rest argv)))
      (finish-output)
      (return-from main 0))
    (when (and (equal (first argv) "--bench") (= (length argv) 2))
      (return-from main (bench scene (second argv))))
    (when argv
      (format *error-output* "usage: lisp.lisp [--ppm W H SS | --bench REPORT.json]~%")
      (finish-output *error-output*)
      (sb-ext:exit :code 2))

    (format t "1. SQRT, the one function the ray tracer needs from the host~%")
    (dolist (src '("SQRT(2)" "SQRT(2, 40)" "SQRT(2.25)" "SQRT(1000000, 3)" "SQRT(0.000)"
                   "SQRT(6.25, 3000)" "SQRT(0.0025, 1)" "SQRT(0.0225, 1)" "SQRT(99.999999, 2)"
                   "SQRT(POWER(12345678901234567890, 2))" "SQRT(POWER(10, 41) + 1, 3)"
                   "SQRT(-4)" "SQRT(\"four\")" "SQRT(4, -1)" "SQRT(4, 0.5)" "SQRT(4, 1000001)"))
      (format t "   ~38a => ~a~%" src
              (handler-case (sel:as-text (sel:run (sel:compile-source src) (sel:make-none)))
                (sel:sel-error (e)
                  (format nil "~a at ~D:~D"
                          (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))

    (format t "2. the scene, 64 x 36, one ray per pixel~%")
    (let* ((img (frame scene 64 36 1))
           (crc (sel:as-text (sel:run (sel:compile-source "CRC32(IMG)")
                                      (sel:from-native (list (cons "IMG" img)))))))
      (format t "   ~D bytes of PPM, CRC32 ~a~%" (length img) crc)))
  0)

(defun nanoseconds ()
  "The monotonic clock. Not GET-INTERNAL-REAL-TIME: on Linux SBCL reads the
coarse clock for it, which moves a millisecond at a time."
  (multiple-value-bind (seconds nanoseconds) (sb-unix:clock-gettime sb-unix:clock-monotonic)
    (+ (* seconds 1000000000) nanoseconds)))

(defun bench (scene report)
  "The frame tools/commit-benchmark/snapshot.py times: 64 x 36, one ray per
pixel, RAYTRACE_WARMUPS (2) unmeasured runs, then RAYTRACE_RUNS (5)."
  (let ((warmups (parse-integer (or (uiop:getenv "RAYTRACE_WARMUPS") "2")))
        (runs (parse-integer (or (uiop:getenv "RAYTRACE_RUNS") "5")))
        (crc (sel:compile-source "CRC32(IMG)"))
        (samples '())
        (outputs '()))
    (dotimes (i (+ warmups runs))
      (let* ((context (sel:from-native (list (cons "W" "64") (cons "H" "36") (cons "SS" "1"))))
             (start (nanoseconds))
             (img (sel:as-text (sel:run scene context)))
             (elapsed (/ (- (nanoseconds) start) 1d6)))
        (when (>= i warmups)
          (push elapsed samples)
          (push (sel:as-text (sel:run crc (sel:from-native (list (cons "IMG" img))))) outputs))))
    ;; By hand: the report is four keys, and the numbers print as JSON wants them.
    (with-open-file (out report :direction :output :if-exists :supersede
                                :external-format :utf-8)
      (format out "{\"samples_ms\": [~{~,6f~^, ~}], \"outputs\": [~{\"~a\"~^, ~}], ~
                   \"warmups\": ~D, \"runs\": ~D}"
              (reverse samples) (reverse outputs) warmups runs))
    0))
