;;;; Shared measurement harness for the Lisp performance queue (LISP-P*).
;;;;
;;;;   SEL_LISP_BENCH_ROOT=/path/to/lisp/   (default lisp/) selects the tree under test, so the same
;;;;   workloads run against a baseline copy and the working tree on the same machine.
;;;;   BENCH_TASKS=LISP-P1,LISP-P3           runs only those tasks (default: all registered)
;;;;   BENCH_REPS=5                          repetitions per point (median and min are reported)
;;;;
;;;; Output is TSV: task  label  n  median_ms  min_ms  mb_consed_per_iter  checksum
;;;; Every point carries a semantic checksum so a speedup that changes an answer is visible.
(require :asdf)
(load (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname)))
(let ((*standard-output* (make-broadcast-stream))
      (root (or (uiop:getenv "SEL_LISP_BENCH_ROOT") "lisp/")))
  (asdf:load-asd (truename (merge-pathnames "sel-lang.asd" (uiop:ensure-directory-pathname root))))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang)
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(defvar *tasks* '())
(defvar *sink* nil)
(defun reps () (let ((e (uiop:getenv "BENCH_REPS"))) (if e (parse-integer e) 5)))

(defmacro deftask (name &body body)
  `(push (cons ,name (lambda () ,@body)) *tasks*))

(defun median (xs) (let ((s (sort (copy-list xs) #'<))) (nth (floor (length s) 2) s)))

(defun bench (task label n fn &key (iters 1) (warmup 1))
  "Runs FN ITERS times per repetition; FN returns a checksum (any printable value)."
  (dotimes (i warmup) (setf *sink* (funcall fn)))
  (let ((times '()) (bytes 0) (sum nil))
    (dotimes (r (reps))
      (sb-ext:gc :full t)
      (let ((b0 (sb-ext:get-bytes-consed)) (t0 (get-internal-real-time)))
        (dotimes (i iters) (setf sum (funcall fn)))
        (push (/ (* 1000d0 (- (get-internal-real-time) t0)) internal-time-units-per-second iters) times)
        (setf bytes (/ (- (sb-ext:get-bytes-consed) b0) (float iters 1d0)))))
    (format t "~a~c~a~c~d~c~,3f~c~,3f~c~,3f~c~a~%" task #\Tab label #\Tab n #\Tab
            (median times) #\Tab (reduce #'min times) #\Tab (/ bytes 1048576d0) #\Tab sum)
    (finish-output)))

(defun run-tasks ()
  (let* ((only (uiop:getenv "BENCH_TASKS"))
         (want (and only (uiop:split-string only :separator ","))))
    (format t "# ~a ~a root=~a reps=~d~%" (lisp-implementation-type) (lisp-implementation-version)
            (or (uiop:getenv "SEL_LISP_BENCH_ROOT") "lisp/") (reps))
    (dolist (task (reverse *tasks*))
      (when (or (null want) (member (car task) want :test #'string=))
        (handler-case (funcall (cdr task))
          (error (e) (format t "~a~cERROR~c~a~%" (car task) #\Tab #\Tab e)))))))

(defun fmt (control &rest args) (apply #'format nil control args))
(defun run-src (src &optional (ctx nil))
  (sel:value-dump (sel:run (sel:compile-source src) (or ctx (sel:make-none)))))
