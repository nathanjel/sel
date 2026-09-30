;;;; LISP-P11 .. LISP-P20 workloads.   sbcl --script tools/perf/lisp/tasks-r2.lisp
;;;;   SEL_LISP_BENCH_ROOT=/path/to/baseline/lisp/ BENCH_TASKS=LISP-P11 sbcl --script tools/perf/lisp/tasks-r2.lisp
(load (merge-pathnames "harness.lisp" *load-truename*))
(in-package :cl-user)

(defun sq (name &rest args) (apply (find-symbol name "SEL.SQL") args))
(defun mb () (sq "BINDING-COLUMN" "v" "t" :num))
(defun text-list-value (n) (sel:from-native (loop for i below n collect (fmt "s~d" i))))
(defun num-list-value (n) (sel:from-native (loop for i below n collect i)))
(defun xbind () (list (cons "X" (sq "BINDING-COLUMN" "x" "t" :text))
                      (cons "N" (sq "BINDING-COLUMN" "n" "t" :num))))
(defun translate* (src bindings &optional (dialect "mariadb"))
  (sq "TRANSLATE" (sel:compile-source src) dialect bindings))
(defun inline-len (f) (length (sq "AS-VALUE" f :inline)))

;; P11 — n-ary folds: IN over a value binding, ANY over a list, IN over n literals
(deftask "LISP-P11"
  (dolist (n '(1600 3200 6400))
    (let ((b (append (xbind) (list (cons "L" (sq "BINDING-VALUE" (text-list-value n)))))))
      (bench "LISP-P11" "X IN L (value binding)" n
             (lambda () (inline-len (translate* "X IN L" b))))
      (bench "LISP-P11" "ANY(L, _ == X)" n
             (lambda () (inline-len (translate* "ANY(L, _ == X)" b))))))
  (dolist (n '(250 1000 4000))
    (let ((src (with-output-to-string (o)
                 (write-string "X IN (" o)
                 (dotimes (i n) (when (> i 0) (write-string ", " o)) (format o "\"a~d\"" i))
                 (write-string ")" o))))
      (bench "LISP-P11" "X IN (n text literals)" n
             (lambda () (inline-len (translate* src (xbind)))))))
  (let ((src (with-output-to-string (o)
               (write-string "COALESCE(X" o)
               (dotimes (i 4000) (format o ", \"c~d\"" i))
               (write-string ")" o))))
    (bench "LISP-P11" "COALESCE of n literals" 4000
           (lambda () (inline-len (translate* src (xbind)))))))

;; P12 — parameters: slot ids, bindings, :params / :inline renders
(deftask "LISP-P12"
  (dolist (n '(3200 6400 12800))
    (let* ((src (with-output-to-string (o)
                  (write-string "X IN (" o)
                  (dotimes (i n) (when (> i 0) (write-string ", " o)) (format o "\"p~d\"" i))
                  (write-string ")" o)))
           (f nil))
      (bench "LISP-P12" "translate n params" n
             (lambda () (setf f (translate* src (xbind))) (length (sq "FRAGMENT-PARAMS" f))))
      (bench "LISP-P12" "as-value :params" n (lambda () (length (sq "AS-VALUE" f :params))))
      (bench "LISP-P12" "bindings" n (lambda () (length (sq "BINDINGS" f))))
      (bench "LISP-P12" "as-value :inline" n (lambda () (length (sq "AS-VALUE" f :inline)))))))

;; P13 — nested interpolation compile (was O(n * depth))
(deftask "LISP-P13"
  (dolist (n '(1000 2000 4000))
    (let ((src (with-output-to-string (o)
                 (dotimes (i n) (write-string "\"{" o)) (write-char #\1 o)
                 (dotimes (i n) (write-string "}\"" o)))))
      (bench "LISP-P13" "nested interpolation (E_DEPTH expected)" n
             (lambda () (handler-case (progn (sel:compile-source src) "ok")
                          (sel:sel-error (e) (sel:sel-error-code e))))))))

;; P14 — RECORD with n literal keys
(deftask "LISP-P14"
  (dolist (n '(2000 4000 8000 16000))
    (let ((src (with-output-to-string (o)
                 (write-string "RECORD(" o)
                 (dotimes (i n) (when (> i 0) (write-string "," o)) (format o "\"k~d\",~d" i i))
                 (write-string ")" o))))
      (bench "LISP-P14" "compile RECORD n keys" n
             (lambda () (length (sel::program-source (sel:compile-source src)))))
      (let ((p (sel:compile-source src)))
        (bench "LISP-P14" "run RECORD n keys" n
               (lambda () (sel:value-size (sel:run p (sel:make-none)))))))))

;; P15 — dialect lookups: a typical rule, repeated; a miss-heavy unsupported walk
(deftask "LISP-P15"
  (let* ((rule "N > 5 AND LEN(X) < 40 AND UPPER(X) $!= \"Q\" AND COALESCE(X, \"d\") $== \"a\"")
         (p (sel:compile-source rule)))
    (bench "LISP-P15" "translate typical 20-node rule x2000" 1
           (lambda () (let ((l 0)) (dotimes (i 2000) (incf l (inline-len (sq "TRANSLATE" p "mariadb" (xbind))))) l)))
    (bench "LISP-P15" "translate typical rule x2000 postgresql" 1
           (lambda () (let ((l 0)) (dotimes (i 2000) (incf l (inline-len (sq "TRANSLATE" p "postgresql" (xbind))))) l))))
  (let ((src (with-output-to-string (o)
               (write-string "RECORD(" o)
               (dotimes (i 300) (when (> i 0) (write-string "," o)) (format o "\"k~d\",LEN(X)+~d" i i))
               (write-string ")" o))))
    (bench "LISP-P15" "plan-hybrid wide RECORD (contains-unsupported walk)" 300
           (lambda () (let ((plan (sq "PLAN-HYBRID" (sel:compile-source (fmt "ORDERS .> MAP(~a)" src)) "mariadb"
                                      (list (cons "ORDERS" (sq "BINDING-RELATION" "orders" "o"
                                                              (list (cons "X" (sq "BINDING-COLUMN" "x" "o" :text)))))))))
                        (if (sq "HYBRID-PLAN-PURE-SQL-P" plan) "pure_sql" "other"))))))

;; P16 — statement chains
(deftask "LISP-P16"
  (dolist (n '(1000 2000 4000))
    (let ((src (with-output-to-string (o)
                 (write-string "A0 = N; " o)
                 (loop for i from 1 to n do (format o "A~d = A~d + 1; " i (1- i)))
                 (format o "A~d > 0" n))))
      (bench "LISP-P16" "statement chain translate (refusal expected)" n
             (lambda () (handler-case (inline-len (translate* src (xbind)))
                          (error (e) (type-of e))))))))

;; P16 (b) — many INDEPENDENT statements (no depth refusal): the assoc list of definitions
(deftask "LISP-P16"
  (dolist (n '(2000 4000 8000))
    (let ((src (with-output-to-string (o)
                 (dotimes (i n) (format o "B~d = N + ~d; " i i))
                 (format o "B0 + B~d > 0" (1- n)))))
      (bench "LISP-P16" "n independent statements, two read" n
             (lambda () (handler-case (inline-len (translate* src (xbind)))
                          (error (e) (type-of e))))))
    (let ((src (with-output-to-string (o)
                 (dotimes (i n) (format o "B~d = N + ~d; " i i))
                 (format o "ANY((~{B~d~^, ~}), _ > 0)" (loop for i below (min n 300) collect i)))))
      (bench "LISP-P16" "n independent statements, 300 read in an ANY" n
             (lambda () (handler-case (inline-len (translate* src (xbind)))
                          (error (e) (type-of e))))))))

;; P17 — unique regex patterns
(deftask "LISP-P17"
  (let ((p (sel:compile-source "RMATCH(P, \"abc\")")))
    (dolist (n '(1000 2500 5000))
      (bench "LISP-P17" "unique patterns through RMATCH" n
             (lambda () (let ((hits 0))
                          (dotimes (i n)
                            (when (eq (sel:value-dump (sel:run p (sel:from-native (list (cons "P" (fmt "ab~dc?" i))))))
                                      nil)
                              (incf hits))
                            (incf hits))
                          hits))))))

;; P18 — TO_HEX
(deftask "LISP-P18"
  (dolist (n '(500000 1000000 2000000))
    (let ((p (sel:compile-source (fmt "LEN(TO_HEX(FROM_HEX(REPEAT(\"ab\", ~d))))" n))))
      (bench "LISP-P18" "TO_HEX n bytes (incl. FROM_HEX)" n
             (lambda () (sel:value-dump (sel:run p (sel:make-none))))))
    (let ((bytes (make-array n :element-type '(unsigned-byte 8) :initial-element 171)))
      (bench "LISP-P18" "bytes-to-hex only" n (lambda () (length (sel::bytes-to-hex bytes)))))))

;; P19 — join-key canonicalisation
(deftask "LISP-P19"
  (dolist (z '(5000 10000 20000))
    (let* ((key (fmt "1.5~a" (make-string z :initial-element #\0)))
           (p (sel:compile-source "COUNT(LINK(A, B, a[\"k\"] == b[\"k\"]))"))
           (ctx (sel:from-native (list (cons "A" (list (list (cons "k" key))))
                                       (cons "B" (list (list (cons "k" "1.5"))))))))
      (bench "LISP-P19" "two-row equi LINK, key with z trailing zeros" z
             (lambda () (sel:value-dump (sel:run p ctx))))))
  (dolist (n '(2000 4000 8000))
    (let* ((p (sel:compile-source "COUNT(LINK(A, B, a[\"k\"] == b[\"k\"]))"))
           (ctx (sel:from-native (list (cons "A" (loop for i below n collect (list (cons "k" (fmt "~d.50" (mod i 50))))))
                                       (cons "B" (loop for i below 50 collect (list (cons "k" (fmt "~d.5" i)))))))))
      (bench "LISP-P19" "equi LINK n left rows x 50 buckets, text decimal keys" n
             (lambda () (sel:value-dump (sel:run p ctx)))))))

;; P20 — list keys
(deftask "LISP-P20"
  (let ((p (sel:compile-source "COUNT(MAP(R, (1, 2, 3, 4, 5, 6)))")))
    (dolist (n '(50000 100000 200000))
      (let ((ctx (sel:from-native (list (cons "R" (loop for i below n collect i))))))
        (bench "LISP-P20" "MAP comma list per row" n (lambda () (sel:value-dump (sel:run p ctx)))))))
  (let ((v (num-list-value 1000)))
    (bench "LISP-P20" "value-hash 1000-int list x200" 1000
           (lambda () (let ((h 0)) (dotimes (i 200) (setf h (sel::value-hash v))) h)))
    (bench "LISP-P20" "value-copy 1000-int list x200" 1000
           (lambda () (let ((c nil)) (dotimes (i 200) (setf c (sel::value-copy v))) (sel:value-size c))))))

(run-tasks)
