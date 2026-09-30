;;;; LISP-P21 .. LISP-P27 workloads.   sbcl --script tools/perf/lisp/tasks-r3.lisp
;;;;   SEL_LISP_BENCH_ROOT=/path/to/baseline/lisp/ BENCH_TASKS=LISP-P21 sbcl --script tools/perf/lisp/tasks-r3.lisp
(load (merge-pathnames "harness.lisp" *load-truename*))
(in-package :cl-user)

(defun sq (name &rest args) (apply (find-symbol name "SEL.SQL") args))
(defun sl (name &rest args) (apply (find-symbol name "SEL") args))

;; P21 — REPEAT / PADL / PADR
(defun text-len (src) (length (sel::value-scalar (sel:evaluate src))))
(deftask "LISP-P21"
  (dolist (n '(1250000 2500000 5000000))
    (let ((n n))
      (bench "LISP-P21" "REPEAT(\"abcd\", n/4) chars" n
             (lambda () (text-len (fmt "REPEAT(\"abcd\", ~d)" (floor n 4)))))
      (bench "LISP-P21" "PADL(\"x\", n, \"ab\")" n
             (lambda () (text-len (fmt "PADL(\"x\", ~d, \"ab\")" n))))
      (bench "LISP-P21" "PADR(\"x\", n, \"-\")" n
             (lambda () (text-len (fmt "PADR(\"x\", ~d, \"-\")" n)))))))

;; P22 — lexer operator matching: a big program and a tiny rule
(defun op-heavy-source (n)
  (with-output-to-string (o)
    (write-string "A" o)
    (dotimes (i n) (format o " + B * 2 - C / 3 ~a D" (if (evenp i) "&" "%")))))
(deftask "LISP-P22"
  (dolist (n '(2500 5000 10000))
    (let ((src (op-heavy-source n)))
      (bench "LISP-P22" "compile-source op-heavy (chars)" (length src)
             (lambda () (progn (sel:compile-source src) "ok")))))
  (bench "LISP-P22" "compile-source tiny rule x2000" 2000
         (lambda () (let ((k 0)) (dotimes (i 2000) (sel:compile-source "IF(A > 1 AND B < 5, A * 2 + B, ROUND(A / 3, 2))") (incf k)) k))
         :iters 1))

;; P23 — evaluate = compile + run once (optimizer cost for nothing)
(deftask "LISP-P23"
  (let ((src "IF(A > 1 AND B < 5, A * 2 + B, ROUND(A / 3, 2))"))
    (bench "LISP-P23" "evaluate x5000" 5000
           (lambda () (let ((r nil))
                        (dotimes (i 5000)
                          (let ((ctx (sel:from-native (list (cons "A" 7) (cons "B" 3)))))
                            (setf r (sel:value-dump (sel:evaluate src ctx)))))
                        r)))
    (bench "LISP-P23" "compile + run x5000 (same as evaluate)" 5000
           (lambda () (let ((r nil))
                        (dotimes (i 5000)
                          (let ((ctx (sel:from-native (list (cons "A" 7) (cons "B" 3)))))
                            (setf r (sel:value-dump (sel:run (sel:compile-source src) ctx)))))
                        r)))
    (let ((p (sel:compile-source src)))
      (bench "LISP-P23" "run a compiled program x5000" 5000
             (lambda () (let ((r nil))
                          (dotimes (i 5000)
                            (let ((ctx (sel:from-native (list (cons "A" 7) (cons "B" 3)))))
                              (setf r (sel:value-dump (sel:run p ctx)))))
                          r))))))

;; P24 — SQL text literals
(defun text-lits-source (n len)
  (with-output-to-string (o)
    (write-string "X IN (" o)
    (dotimes (i n)
      (when (> i 0) (write-string ", " o))
      (format o "\"~a~d'q\\\\z\"" (make-string len :initial-element #\a) i))
    (write-string ")" o)))
(deftask "LISP-P24"
  (let ((bind (list (cons "X" (sq "BINDING-COLUMN" "x" "t" :text)))))
    (dolist (n '(12500 25000 50000))
      (let ((src (text-lits-source n 44)))
        (bench "LISP-P24" "inline render of n 44-char literals (mariadb)" n
               (lambda () (length (sq "AS-VALUE" (sq "TRANSLATE" (sel:compile-source src) "mariadb" bind) :inline))))))
    (let ((lit (concatenate 'string (make-string 44 :initial-element #\a) "'q\\z")))
      (dolist (d '("mariadb" "postgresql" "sqlite"))
        (bench "LISP-P24" (fmt "emit-text-literal x100000, 44 chars (~a)" d) 100000
               (lambda () (let ((k 0)) (dotimes (i 100000) (incf k (length (sq "EMIT-TEXT-LITERAL" d lit)))) k)))))
    (bench "LISP-P24" "inline render of n 44-char literals (postgresql)" 25000
           (lambda () (length (sq "AS-VALUE" (sq "TRANSLATE" (sel:compile-source (text-lits-source 25000 44)) "postgresql" bind) :inline))))
    (let* ((big (make-string 1000000 :initial-element #\a))
           (src (fmt "X $== \"~a\"" big)))
      (bench "LISP-P24" "one 1 MB literal" 1000000
             (lambda () (length (sq "AS-VALUE" (sq "TRANSLATE" (sel:compile-source src) "mariadb" bind) :inline)))))))

;; P25 — hybrid helper bookkeeping: chain of non-literal helpers before a pipeline
(deftask "LISP-P25"
  (let ((orders (sq "BINDING-RELATION" "orders" "orders"
                    (list (cons "ID" (sq "BINDING-COLUMN" "id" "orders"))
                          (cons "AMOUNT" (sq "BINDING-COLUMN" "amount" "orders"))))))
    (dolist (n '(50 100 150 200 250 300))
      (let ((src (with-output-to-string (o)
                   (write-string "H0 = COUNT(ORDERS); " o)
                   (loop for i from 1 to n do (format o "H~d = H~d + COUNT(ORDERS); " i (1- i)))
                   (format o "ORDERS .> FILTER(_['amount'] > H~d) .> SORT_BY(_['amount']) .> MAP(RECORD('id', _['id']))" n))))
        (bench "LISP-P25" "plan-hybrid, n non-literal helpers" n
               (lambda ()
                 (let ((plan (sq "PLAN-HYBRID" (sel:compile-source src) "postgresql" (list (cons "ORDERS" orders)))))
                   (format nil "~a" (type-of plan)))))))))

;; P26 — record shapes sharing a long key prefix
(deftask "LISP-P26"
  (dolist (nshapes '(64 128 256))
    (bench "LISP-P26" "n shapes with a 6-key common prefix, 20 passes" nshapes
           (lambda ()
             (let ((k 0))
               (dotimes (pass 20)
                 (dotimes (s nshapes)
                   (let ((keys (append (list "a" "b" "c" "d" "e" "f") (list (fmt "x~d" s) "tail"))))
                     (sel::get-record-shape keys) (incf k))))
               k)))))

;; P27 — smaller items
(deftask "LISP-P27"
  ;; registry lookups per call node (compile a call-heavy rule)
  (let ((src (with-output-to-string (o)
               (write-string "ABS(1)" o) (dotimes (i 4000) (format o " + MAX(~d, 2) + LEN(\"x\")" i)))))
    (bench "LISP-P27" "compile-source 12000 call nodes" 12000
           (lambda () (progn (sel:compile-source src) "ok"))))
  ;; ISNUM parsing twice
  (let ((p (sel:compile-source "SUM(L, X, IF(ISNUM(X) AND X > 5, X, 0))"))
        (ctx (sel:from-native (list (cons "L" (loop for i below 100000 collect (fmt "~d.5" i)))))))
    (bench "LISP-P27" "ISNUM(X) AND X > 5 over 100k numeric texts" 100000
           (lambda () (sel:value-dump (sel:run p ctx)))))
  ;; BIN copy
  (let ((p (sel:compile-source "B = FROM_HEX(REPEAT(\"ab\", 500000)); N = 0; R = LIST(); MAP(LIST(1,2,3,4,5,6,7,8), _, (C = B; N += BLEN(C)) ); N")))
    (bench "LISP-P27" "copy a 500 KB BIN 8 times (assignment)" 8
           (lambda () (sel:value-dump (sel:run p nil)))))
  ;; BIN copy in isolation: one 500 KB BIN in the context, copied by assignment 200 times
  (let* ((bytes (make-array 500000 :element-type '(unsigned-byte 8) :initial-element 171))
         (p (sel:compile-source "N = 0; MAP(LIST(1,2,3,4,5,6,7,8,9,10), _, (C = B; N += 1)); N")))
    (bench "LISP-P27" "assign a 500 KB BIN, 10 copies x 20 runs" 200
           (lambda () (let ((r nil))
                        (dotimes (i 20)
                          (let ((ctx (sel:make-none)))
                            (sel::value-set ctx "B" (sel::make-bin bytes))
                            (setf r (sel:value-dump (sel:run p ctx)))))
                        r))))
  ;; decimal division
  (let ((p (sel:compile-source "SUM(L, X, X / 7 + X / 3)"))
        (ctx (sel:from-native (list (cons "L" (loop for i below 100000 collect i))))))
    (bench "LISP-P27" "100k fixnum divisions x2" 200000
           (lambda () (sel:value-dump (sel:run p ctx)))))
  ;; strict-call args structs: non-math strict builtins over text
  (let ((p (sel:compile-source "SUM(L, X, LEN(LEFT(X, 2)) + LEN(UPPER(X)) + LEN(TRIM(X)))"))
        (ctx (sel:from-native (list (cons "L" (loop for i below 100000 collect (fmt "item~d" i)))))))
    (bench "LISP-P27" "strict text calls LEFT/UPPER/TRIM/LEN over 100k" 500000
           (lambda () (sel:value-dump (sel:run p ctx))))))

(run-tasks)
