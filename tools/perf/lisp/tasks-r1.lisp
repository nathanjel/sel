;;;; LISP-P1 .. LISP-P10 workloads.   sbcl --script tools/perf/lisp/tasks-r1.lisp
(load (merge-pathnames "harness.lisp" *load-truename*))
(in-package :cl-user)

(defun rows (n &key (fields 4))
  "N alist records {id, a, b, c[, d...]}: deterministic (seeded by the index), no RNG."
  (loop for i below n
        collect (append (list (cons "id" i) (cons "a" (mod (* i 7919) 101))
                              (cons "b" (mod (* i 104729) 13)) (cons "c" (mod (* i 31) 1000)))
                        (loop for f from 5 to fields collect (cons (fmt "f~d" f) i)))))
(defun ctx-of (&rest kv) (sel:from-native (loop for (k v) on kv by #'cddr collect (cons k v))))

;; P1 — interpolation count in one literal (emit-parts (length acc) was quadratic)
(deftask "LISP-P1"
  (dolist (n '(4000 16000 64000))
    (let ((src (with-output-to-string (o) (write-char #\" o) (dotimes (i n) (write-string "{1}" o)) (write-char #\" o))))
      (bench "LISP-P1" "interp-literal-compile" n
             (lambda () (length (sel::program-source (sel:compile-source src))))))))

;; P2 — legal large numbers
(deftask "LISP-P2"
  (dolist (n '(100000 200000 400000))
    (let* ((s (make-string n :initial-element #\7)))
      (bench "LISP-P2" "dec-parse" n (lambda () (sel::dec-digits (sel::dec-parse s))))
      (let ((d (sel::dec-parse s)))
        (bench "LISP-P2" "dec-format" n (lambda () (length (sel::dec-format d)))))
      (let ((src (fmt "~a + 1" s)))
        (bench "LISP-P2" "plus-one" n (lambda () (length (run-src src)))))
      (let ((src (fmt "~a == ~a" s s)))
        (bench "LISP-P2" "equals" n (lambda () (run-src src))))))
  (dolist (k '(2000 4000 8000))
    (let ((src (fmt "POWER(9999999999, ~d)" k)))
      (bench "LISP-P2" "power-9999999999-k" k (lambda () (length (run-src src))))))
  (dolist (k '(25000 50000 100000))
    (let ((src (fmt "POWER(3.14159, ~d)" k)))
      (bench "LISP-P2" "power-3.14159-k" k (lambda () (length (run-src src)))))))

;; P3 / P5 — operator dispatch and literal handling in a FILTER predicate
(deftask "LISP-P3"
  (dolist (n '(50000 100000 200000))
    (let ((ctx (ctx-of "X" (rows n))))
      (dolist (src '("X .> FILTER(_[\"a\"] > 10 AND _[\"b\"] < 5 AND _[\"a\"] != 77) .> COUNT"
                     "COUNT(FILTER(X, _[\"a\"] + _[\"b\"] * 2 >= 40))"))
        (let ((p (sel:compile-source src)))
          (bench "LISP-P3" (subseq src 0 (min 28 (length src))) n
                 (lambda () (sel:value-dump (sel:run p ctx)))))))))

(deftask "LISP-P5"
  (let ((ctx (ctx-of "A" 7 "B" 3)))
    (dolist (src '("A > 1" "A == 5 AND B != 3"))
      (let ((p (sel:compile-source src)))
        (bench "LISP-P5" (fmt "optimised ~a" src) 1
               (lambda () (dotimes (i 100000) (sel:run p ctx)) (sel:value-dump (sel:run p ctx)))
               :iters 1)
        (bench "LISP-P5" (fmt "plain-tree ~a" src) 1
               (lambda () (let ((ast (sel::program-ast p)))
                            (dotimes (i 100000) (sel::eval-node ast (sel::make-context ctx)))
                            (sel:value-dump (sel::eval-node ast (sel::make-context ctx)))))
               :iters 1)))))

;; P4 — text-key sorts
(deftask "LISP-P4"
  (dolist (n '(12500 25000 50000))
    (let* ((recs (loop for i below n
                       collect (list (cons "id" i)
                                     (cons "name" (fmt "name-~36r-~d" (mod (* i 2654435761) 1000003) (mod i 97))))))
           (ctx (ctx-of "R" recs))
           (p1 (sel:compile-source "COUNT(SORT_BY(R, _[\"name\"]))"))
           (p2 (sel:compile-source "COUNT(TOP(SORT_BY(R, _[\"name\"]), 3))"))
           (p3 (sel:compile-source "SORT_BY(R, _[\"name\"]) .> TAKE(1) .> JOIN(\",\")")))
      (bench "LISP-P4" "sort-by-text" n (lambda () (sel:value-dump (sel:run p1 ctx))))
      (bench "LISP-P4" "top3-text" n (lambda () (sel:value-dump (sel:run p2 ctx))))
      (bench "LISP-P4" "sort-by-text-take1" n (lambda () (sel:value-dump (sel:run p3 ctx)))))))

;; P6 — short-number conversion (micro, 1M iterations per sample)
(deftask "LISP-P6"
  (dolist (s '("12345.67" "12345" "0" "-0.5" "123456789012345678"))
    (bench "LISP-P6" (fmt "dec-parse ~a (1M)" s) 1
           (lambda () (let ((x nil)) (dotimes (i 1000000) (setf x (sel::dec-parse s))) (sel::dec-format x))))
    (let ((d (sel::dec-parse s)))
      (bench "LISP-P6" (fmt "dec-format ~a (1M)" s) 1
             (lambda () (let ((x nil)) (dotimes (i 1000000) (setf x (sel::dec-format d))) x))))))

;; P7 — DEDUPE over distinct BINs
(deftask "LISP-P7"
  (dolist (n '(2000 4000 8000))
    (let* ((bins (loop for i below n
                       collect (coerce (list (ldb (byte 8 0) i) (ldb (byte 8 8) i) 7 9) '(vector (unsigned-byte 8)))))
           (ctx (ctx-of "B" bins))
           (p (sel:compile-source "COUNT(DEDUPE(B))")))
      (bench "LISP-P7" "dedupe-bin" n (lambda () (sel:value-dump (sel:run p ctx)))))))

;; P8 — equi-join on unshaped vs shaped rows
(deftask "LISP-P8"
  (dolist (n '(6250 12500 25000))
    (let* ((left (rows n)) (right (rows (floor n 2)))
           (ctx (ctx-of "L" left "R" right))
           (p (sel:compile-source "COUNT(LINK(L, R, l, r, l[\"id\"] == r[\"id\"]))")))
      (bench "LISP-P8" "link-equi-shaped-rows" n (lambda () (sel:value-dump (sel:run p ctx)))))
    ;; unshaped: rows built as plain lists of children through value-set with a repeated/irregular key order
    (let* ((mk (lambda (i)
                 (let ((v (sel:make-none)))
                   (if (evenp i)
                       (progn (sel::value-set v "id" (sel:make-int i)) (sel::value-set v "z" (sel:make-int 1)))
                       (progn (sel::value-set v "z" (sel:make-int 1)) (sel::value-set v "id" (sel:make-int i))))
                   v)))
           (left (sel:make-list-value (loop for i below n collect (funcall mk i))))
           (right (sel:make-list-value (loop for i below (floor n 2) collect (funcall mk i))))
           (ctx (sel:make-none)))
      (sel::value-set ctx "L" left) (sel::value-set ctx "R" right)
      (let ((p (sel:compile-source "COUNT(LINK(L, R, l, r, l[\"id\"] == r[\"id\"]))")))
        (bench "LISP-P8" "link-equi-unshaped-rows" n (lambda () (sel:value-dump (sel:run p ctx))))))))

;; P9 — nested-loop LINK and AND-conjunction joins
(deftask "LISP-P9"
  (dolist (n '(175 350 700))
    (let* ((ctx (ctx-of "L" (rows n) "R" (rows n)))
           (p (sel:compile-source "COUNT(LINK(L, R, l, r, l[\"c\"] < r[\"c\"] AND l[\"id\"] != r[\"id\"]))")))
      (bench "LISP-P9" "link-nested-loop-non-equi" n (lambda () (sel:value-dump (sel:run p ctx))))))
  (dolist (n '(250 500 1000))
    (let* ((ctx (ctx-of "L" (rows n) "R" (rows n)))
           (p (sel:compile-source "COUNT(LINK(L, R, l, r, l[\"a\"] == r[\"a\"] AND l[\"c\"] < r[\"c\"]))")))
      (bench "LISP-P9" "link-equi-and-residual" n (lambda () (sel:value-dump (sel:run p ctx)))))))


;; P10 — execute-hybrid with a large caller context
(deftask "LISP-P10"
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> MAP(RECORD('id', _['id'], 'g', RGROUPS('([0-9]+)', _['id'])))"))
         (plan (sel.sql:plan-hybrid p "postgresql" (list (cons "ORDERS" orders))))
         (runner (lambda (sql params) (declare (ignore sql params))
                   (sel:evaluate "LIST(RECORD('id', 'vip-42', 'amount', 150), RECORD('id', 'reg-99', 'amount', 200))"))))
    (dolist (n '(0 75000 150000 300000))
      (let ((ctx (sel:make-none)))
        (sel::value-set ctx "BIG" (sel:from-native (loop for i below n collect i)))
        (bench "LISP-P10" "execute-hybrid-big-context (20 calls)" n
               (lambda () (let ((r nil)) (dotimes (i 20) (setf r (sel.sql:execute-hybrid plan runner ctx)))
                            (sel:value-dump r))))))))

(run-tasks)
