;;;; API parity probe — Common Lisp. See tools/api.mjs for what this is and why.
;;;; The drivers (one per host) must stay in the same order with the same probe names; the
;;;; diff between their reports is the whole mechanism.

(in-package #:sel-cli)

;;; The kind values are keywords here; the report prints the spelling every host
;;; uses so the diff compares like with like.
(defun kind-name (k) (string-upcase (symbol-name k)))

(defun main ()
  (reset-probes)

  ;; --- kind constants and predicates
  (say "kind.const.none" (kind-name :none))
  (say "kind.const.text" (kind-name :text))
  (say "kind.const.bin" (kind-name :bin))
  (say "kind.const.bool" (kind-name :bool))
  (say "kind.static.bool" (kind-name :bool))
  (say "kind.of.text" (kind-name (sel:value-kind (sel:evaluate "\"x\""))))
  (say "kind.of.bool" (kind-name (sel:value-kind (sel:evaluate "TRUE"))))
  (say "kind.of.none" (kind-name (sel:value-kind (sel:evaluate "(1,2)"))))
  (say "pred.isText" (yn (sel:value-text-p (sel:evaluate "\"x\""))))
  (say "pred.isBool" (yn (sel:value-bool-p (sel:evaluate "TRUE"))))
  (say "pred.isNone" (yn (sel:value-none-p (sel:evaluate "(1,2)"))))
  (say "pred.isBin" (yn (sel:value-bin-p (sel:evaluate "TO_UTF8(\"x\")"))))
  (say "pred.isText.on.bool" (yn (sel:value-text-p (sel:evaluate "TRUE"))))

  ;; --- constructors
  (say "ctor.text" (sel:value-dump (sel:make-text "hi")))
  (say "ctor.bool" (sel:value-dump (sel:make-bool t)))
  (say "ctor.none" (sel:value-dump (sel:make-none)))
  (say "ctor.num.canonicalises" (sel:value-dump (sel:make-num "007")))
  (say "ctor.int" (sel:value-dump (sel:make-int -3)))
  (say "ctor.list" (sel:value-dump (sel:make-list-value
                                    (list (sel:make-text "a") (sel:make-text "b")))))

  ;; --- children, and the ordering rules
  (let ((v (sel:make-none)))
    (sel:value-set v "b" (sel:make-text "1"))
    (sel:value-set v "a" (sel:make-text "2"))
    (say "children.size" (format nil "~d" (sel:value-size v)))
    (say "children.size.is.callable" (yn (and (fboundp 'sel:value-size) t)))
    (say "children.keys" (format nil "~{~a~^,~}" (sel:value-keys v)))
    (sel:value-set v "b" (sel:make-text "9"))
    (say "children.reassign.keeps.position" (format nil "~{~a~^,~}" (sel:value-keys v)))
    (say "children.reassign.no.growth" (format nil "~d" (sel:value-size v)))
    (say "children.has" (yn (sel:value-has v "a")))
    (say "children.has.missing" (yn (sel:value-has v "zz")))
    (say "children.get" (sel:value-dump (sel:value-get v "b"))))

  ;; --- scalar context
  (say "scalar.asText" (sel:as-text (sel:evaluate "\"héllo\"")))
  (say "scalar.asBool" (yn (sel:as-bool (sel:evaluate "TRUE"))))
  (say "scalar.takes.first.child" (sel:as-text (sel:evaluate "(7,8)")))
  (say "scalar.looksNumeric" (yn (sel:looks-numeric (sel:evaluate "\"2.50\""))))
  (say "scalar.looksNumeric.no" (yn (sel:looks-numeric (sel:evaluate "\"x\""))))

  ;; --- equality and dump
  (say "eql.same" (yn (sel:value-eql (sel:make-text "5") (sel:make-text "5"))))
  (say "eql.not.normalised" (yn (sel:value-eql (sel:make-text "5.00") (sel:make-text "5"))))
  (say "dump.tree" (sel:value-dump (sel:evaluate "A=1; A[2]=\"x\"; A")))

  ;; --- programs
  (let ((p (sel:compile-source "IF(A > B, A, C)")))
    (say "program.dependencies" (format nil "~{~a~^ ~}" (sel:dependencies p))))
  (say "program.deps.excludes.assigned"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "X = 1; X + Y"))))
  (say "program.deps.excludes.binder"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "ALL(I, IT, IT > 0)"))))
  ;; A PARENTHESISED binder is still treated as a binding name here, though the
  ;; evaluator rejects it: ALL(I, (IT), IT > 0) is E_EXPECT_SYMBOL when run, yet
  ;; dependencies() answers as if IT were bound. Pinned because all seven hosts
  ;; agree on it and nothing else records it -- not endorsed. See the note in
  ;; docs/contributing.md.
  (say "program.deps.grouped.binder"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "ALL(I, (IT), IT > 0)"))))
  ;; The binding forms of spec/builtins.json (see tools/api.mjs).
  (say "program.deps.forms.top.binder-and-limit"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "TOP(L, X, X[\"a\"], N)"))))
  (say "program.deps.forms.top.limit-is-outer"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "TOP(L, COUNT(_))"))))
  (say "program.deps.forms.sort-by.text-direction-wins"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "SORT_BY(L, K, \"DESC\")"))))
  (say "program.deps.forms.top-by.direction-is-outer"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "TOP_BY(L, _[\"a\"], N, D)"))))
  (say "program.deps.forms.bucket.projection-inside"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "BUCKET(L, G, G[\"k\"], COUNT(G) + _K)"))))
  (say "program.deps.forms.link.named-binders"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "LINK(A, B, X, Y, X[\"a\"] == Y[\"b\"] AND Z)"))))
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "TOTAL" (sel:make-num "59.97"))
    (say "program.run.reads.context" (sel:value-dump (sel:evaluate "TOTAL > 10.00" ctx)))
    (sel:evaluate "SEEN = TOTAL * 2" ctx)
    (say "program.run.mutates.context" (sel:as-text (sel:value-get ctx "SEEN"))))
  ;; A BOOL a host hands in is an ordinary value (see tools/api.mjs).
  (let ((a (sel:make-none)) (b (sel:make-none)))
    (sel:value-set a "FLAG" (sel:make-bool t))
    (sel:value-set b "FLAG" (sel:make-bool t))
    (sel:run (sel:compile-source "FLAG[\"k\"] = 1; 0") a)
    (say "bool.isolated.between.contexts"
         (format nil "~d ~d ~a" (sel:value-size (sel:value-get b "FLAG")) (sel:value-size (sel:make-bool t))
                 (sel:as-text (sel:evaluate "COUNT(TRUE)")))))
  (say "registry.count" (format nil "~d" (length (sel:function-names))))
  (say "registry.sorted.first" (first (sel:function-names)))

  ;; --- errors
  (handler-case (sel:evaluate (format nil "1 +~%  X"))
    (sel:sel-error (e)
      (say "error.code" (sel:sel-error-code e))
      (say "error.line" (format nil "~d" (sel:sel-error-line e)))
      (say "error.col" (format nil "~d" (sel:sel-error-col e)))
      (say "error.isSelError" (yn (typep e 'sel:sel-error)))))
  (handler-case (sel:compile-source "NOPE(1)")
    (sel:sel-error (e) (say "error.compile.unknown.func" (sel:sel-error-code e))))
  (handler-case (sel:make-num "x")
    (sel:sel-error (e) (say "error.host.badnum" (sel:sel-error-code e))))
  ;; Every character is a digit, so this is E_RANGE and not E_NOT_NUM. MAKE-NUM
  ;; is public API, so an embedding application can reach the numeral cap without
  ;; compiling a rule at all -- and every host must refuse it the same way.
  (handler-case (sel:make-num (make-string 2000001 :initial-element #\1))
    (sel:sel-error (e) (say "error.host.hugenum" (sel:sel-error-code e))))

  ;; Every public constructor holds the same rules (spec §8):
  ;; the decimal form within the caps and canonical, keys checked, a malformed
  ;; call E_BAD_ARG -- each host through its own spelling of the constructor.
  (loop for (name . build)
          in (list (cons "error.host.dec.fraccap" (lambda () (sel:make-num (sel:dec-make nil 1 1000001))))
                   (cons "error.host.dec.negscale" (lambda () (sel:make-num (sel:dec-make nil 7 -1))))
                   (cons "error.host.key.utf8"
                         (lambda () (sel:from-native (list (cons (coerce (list #\a (code-char #xD800)) 'string) "1")))))
                   (cons "error.host.malformed" (lambda () (sel:make-int 3/2))))
        do (handler-case (progn (funcall build) (say name "no error"))
             (sel:sel-error (e) (say name (sel:sel-error-code e)))))
  (say "ctor.dec.negzero" (sel:value-dump (sel:make-num (sel:dec-make t 0 0))))

  ;; A value nested past the cap is refused by every walk of it. Reachable from the
  ;; host API with no source involved at all -- set() does not refuse, because a
  ;; value is built from the leaf up and nothing knows how deep it will end up --
  ;; so the operations that walk it are where the cap has to hold. The numeral cap
  ;; probed two lines up is the same shape of rule.
  (flet ((nest (n)
           (let ((v (sel:make-text "x")))
             (dotimes (i n v)
               (let ((p (sel:make-none)))
                 (sel:value-set p "1" v)
                 (setf v p))))))
    (say "value.depth.under"
         (if (plusp (length (sel:value-dump (nest 199)))) "ok" "no"))
    (handler-case (sel:value-dump (nest 200))
      (sel:sel-error (e) (say "value.depth.over" (sel:sel-error-code e)))))

  ;; dependencies() walks the tree without evaluating it, so it is bounded by
  ;; neither the parser's nesting depth nor the evaluator's -- and in every host
  ;; it was bounded by nothing at all, until a flat chain of about fifty thousand
  ;; operators found the host's own stack. It shares the evaluation cap now, and
  ;; trips at the same node: a program whose dependencies cannot be computed is
  ;; exactly a program that could not have been evaluated. Both sides are pinned,
  ;; because a walk that counts twice or not at all fails one of them.
  (flet ((chain (n)
           (with-output-to-string (s)
             (write-string "A" s)
             (dotimes (i n) (write-string "+A" s)))))
    (say "deps.depth.under"
         (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source (chain 199)))))
    (handler-case (sel:dependencies (sel:compile-source (chain 200)))
      (sel:sel-error (e)
        (say "deps.depth.over"
             (format nil "~a ~d:~d" (sel:sel-error-code e)
                     (sel:sel-error-line e) (sel:sel-error-col e))))))

  ;; --- the physical tree (docs/internals/sql-translation.md 12.1; SEL-0044, SEL-0049).
  ;; One tree per AST (EQ here), the same whatever data ran, the AST
  ;; untouched, RUN the same after an explicit build.
  (let* ((joined (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _1[\"customer_id\"] == _2[\"id\"]) .> FILTER(_[\"orders\"][\"amount\"] > 1)"))
         (before (format nil "~{~a~^ ~}" (sel:dependencies joined)))
         (first (sel:program-physical-ast joined)))
    (say "program.physical.built-once" (yn (eq (sel:program-physical-ast joined) first)))
    (say "program.physical.keeps.ast"
         (format nil "~a ~a" (yn (equal (format nil "~{~a~^ ~}" (sel:dependencies joined)) before)) before))
    (let ((data (sel:make-none)))
      (sel:run (sel:compile-source "ORDERS = LIST(RECORD(\"id\", 1, \"customer_id\", 7, \"amount\", 5), RECORD(\"id\", 2, \"customer_id\", 7, \"amount\", 0), RECORD(\"id\", 3, \"customer_id\", 9, \"amount\", 9)); CUSTOMERS = LIST(RECORD(\"id\", 7, \"name\", \"x\")); 0") data)
      (say "program.physical.run.agrees" (sel:value-dump (sel:run joined data))))
    (let ((empty (sel:make-none)))
      (sel:run (sel:compile-source "ORDERS = LIST(); CUSTOMERS = LIST(); 0") empty)
      (sel:run joined empty))
    (say "program.physical.independent.of.data" (yn (eq (sel:program-physical-ast joined) first))))

  ;; --- host functions (spec/SPEC.md 8.1). Registered before compiling, called
  ;; like a builtin, handed evaluated arguments and the typed readers, never
  ;; allowed to replace a builtin; a compiled program keeps its function.
  (sel:register-function "host_join" 1 3
    (lambda (a)
      (sel:make-text (format nil "~{~a~^|~}"
                             (loop for i below (sel:args-count a) collect (sel:args-text a i))))))
  (sel:register-function "HOST_CHECK" 1 1
    (lambda (a)
      (when (string= (sel:args-text a 0) "")
        (sel:fail "E_BAD_ARG" "must not be empty" (sel:args-pos-of a 0)))
      (sel:make-bool t)))
  (say "host.fn.call" (sel:as-text (sel:evaluate "HOST_JOIN(\"a\", 1, \"c\")")))
  (say "host.fn.case" (sel:as-text (sel:evaluate "host_join(\"x\")")))
  (say "host.fn.listed" (yn (member "HOST_JOIN" (sel:function-names) :test #'string=)))
  (say "host.fn.deps" (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "HOST_JOIN(X, Y)"))))
  (say "host.fn.order" (sel:as-text (sel:evaluate "A = 1; HOST_JOIN((A = A + 1), (A = A * 10), A)")))
  (loop for (name src) in '(("host.fn.arity" "HOST_JOIN()") ("host.fn.type" "HOST_JOIN(\"a\", TRUE)")
                            ("host.fn.error" "HOST_CHECK(\"\")"))
        do (handler-case (progn (sel:evaluate src) (say name "no error"))
             (sel:sel-error (e)
               (say name (format nil "~a ~d:~d" (sel:sel-error-code e)
                                 (sel:sel-error-line e) (sel:sel-error-col e))))))
  (loop for (name fname lo hi) in '(("host.fn.refuse.builtin" "len" 1 1) ("host.fn.refuse.reserved" "and" 1 1)
                                    ("host.fn.refuse.underscore" "_x" 1 1) ("host.fn.refuse.digit" "1x" 1 1)
                                    ("host.fn.refuse.dash" "a-b" 1 1) ("host.fn.refuse.min-over-max" "bad" 2 1)
                                    ("host.fn.refuse.negative" "bad" -1 0))
        do (say name (handler-case
                         (progn (sel:register-function fname lo hi
                                                       (lambda (a) (declare (ignore a)) (sel:make-text "")))
                                "accepted")
                       (sel:sel-error (e) (format nil "SelError ~a" (sel:sel-error-code e)))
                       (error () "refused"))))
  (sel:register-function "HOST_V" 0 0 (lambda (a) (declare (ignore a)) (sel:make-text "old")))
  (let ((early (sel:compile-source "HOST_V()")))
    (sel:register-function "HOST_V" 0 0 (lambda (a) (declare (ignore a)) (sel:make-text "new")))
    (say "host.fn.replace" (format nil "~a ~a" (sel:as-text (sel:run early))
                                   (sel:as-text (sel:evaluate "HOST_V()")))))

  ;; --- dependencies() is FLOW-SENSITIVE (spec/SPEC.md 8): a variable is a
  ;; dependency when some read of it can happen before the program has definitely
  ;; assigned it, in evaluation order. Assignments under a condition, a short
  ;; circuit, `??` or an aggregate body are not definite; `op=` and `A[k] op= x`
  ;; read their target; a plain `A[k] = x` creates A and reads only the index.
  (flet ((deps (src)
           (let ((names (sel:dependencies (sel:compile-source src))))
             (if names (format nil "~{~a~^ ~}" names) "-"))))
    (say "program.deps.read-before-assign" (deps "A + 1; A = 2"))
    (say "program.deps.compound-assign-reads" (deps "X += 1"))
    (say "program.deps.index-compound-reads" (deps "A[1] += 1"))
    (say "program.deps.index-assign-vivifies" (deps "A[1] = 2"))
    (say "program.deps.self-assign-reads" (deps "A = A + 1"))
    (say "program.deps.assign-then-read" (deps "A = 1; A + B"))
    (say "program.deps.conditional-assign" (deps "IF(X, A = 1, 0); A"))
    (say "program.deps.both-branches-assign" (deps "IF(X, A = 1, A = 2); A"))
    (say "program.deps.and-rhs-assign" (deps "X AND (A = 1); A"))
    (say "program.deps.coalesce-rhs-assign" (deps "X ?? (A = 1); A"))
    (say "program.deps.aggregate-body-assign" (deps "MAP(L, A = _); A"))
    (say "program.deps.cond-with-default-assigns" (deps "COND(X, A = 1, Y, A = 2, A = 3); A"))
    (say "program.deps.assign-in-argument" (deps "LEFT(\"abc\", (N = 2)); N"))
    (say "program.deps.compound-rhs-assign-is-too-late" (deps "A += (A = 1; 2); A"))
    (say "program.deps.index-expr-assign-precedes-compound-read" (deps "A[(A = RECORD(\"x\", 1); \"x\")] += 2; A[\"x\"]"))
    (say "program.deps.get-default-assign-not-definite" (deps "GET(R, \"a\", (A = 1)); A"))
    (say "program.deps.index-keys-run-in-source-order" (deps "A[(K = 1)][K] = B; K"))
    (say "program.deps.index-key-read-before-a-later-key-assigns" (deps "A[K][(K = 1)] = B; K"))
    (say "program.deps.top-arg-is-not-a-binder-in-the-three-argument-form" (deps "L = LIST(1,2); TOP(L, A, (A = 1; 1))"))
    (say "program.deps.bucket-key-phase-assignment-is-not-definite-for-the-projection" (deps "L = LIST(1,2); BUCKET(L, G, (A = G; A), COUNT(G) + A)")))

  ;; --- a Program is reusable: after a caught error it runs again, and two
  ;; contexts are independent whatever the interleaving.
  (let* ((divide (sel:compile-source "A / B"))
         (bad (sel:make-none))
         (good (sel:make-none)))
    (sel:run (sel:compile-source "A = 1; B = 0; 0") bad)
    (sel:run (sel:compile-source "A = 6; B = 3; 0") good)
    (flet ((attempt (ctx)
             (handler-case (sel:value-dump (sel:run divide ctx))
               (sel:sel-error (e) (format nil "~a ~d:~d" (sel:sel-error-code e)
                                          (sel:sel-error-line e) (sel:sel-error-col e))))))
      (say "program.reuse.after-error"
           (format nil "~a|~a|~a" (attempt bad) (attempt good) (attempt bad)))))
  (let* ((bump (sel:compile-source "X = X + 1"))
         (a (sel:make-none))
         (c (sel:make-none)))
    (sel:run (sel:compile-source "X = 1; 0") a)
    (sel:run (sel:compile-source "X = 10; 0") c)
    (say "program.reuse.two-contexts"
         (format nil "~a ~a ~a ~a"
                 (sel:as-text (sel:run bump a)) (sel:as-text (sel:run bump c))
                 (sel:as-text (sel:run bump a)) (sel:as-text (sel:run bump c)))))

  ;; --- input the API cannot take is E_BAD_ARG, never a host condition or a
  ;; different SEL error (spec/SPEC.md 8). A statically typed host cannot be handed
  ;; a non-string source and prints n/a; tools/check-api.sh leaves an n/a line out
  ;; of the diff for that host. (Lisp is dynamically typed: every one is posed.)
  (flet ((code (thunk)
           (handler-case (progn (funcall thunk) "no error")
             (sel:sel-error (e) (sel:sel-error-code e))
             (error (e) (format nil "host:~a" (type-of e))))))
    (say "error.compile.non-string" (code (lambda () (sel:compile-source 12))))
    (say "value.native.unsupported" (code (lambda () (sel:from-native #'car))))
    (say "value.native.fraction" (code (lambda () (sel:from-native 1.5d0))))
    (say "host.fn.refuse.not-callable"
         (handler-case (progn (sel:register-function "bad" 0 0 nil) "accepted")
           (sel:sel-error (e) (format nil "SelError ~a" (sel:sel-error-code e)))
           (error () "refused")))
    (sel:register-function "HOST_OOB" 1 2
      (lambda (a) (sel:make-text (sel:args-text a (if (> (sel:args-count a) 1) 1 5)))))
    (say "host.fn.arg.out-of-range" (code (lambda () (sel:evaluate "HOST_OOB(\"x\")")))))


  ;; --- a host-supplied value nested past the cap, handed to RECORD beside a
  ;; key that is not text. Arguments are evaluated first and coerced after (spec/SPEC.md 6.2),
  ;; so the key's E_NOT_TEXT wins; copying the over-deep value (E_DEPTH) happens only once the
  ;; arguments are known good. C++ built the pair in one expression and let the copy run first.
  (let ((ctx (sel:make-none))
        (v (sel:make-text "x")))
    (dotimes (i 300)
      (let ((p (sel:make-none)))
        (sel:value-set p "1" v)
        (setf v p)))
    (sel:value-set ctx "V" v)
    (flet ((at (src)
             (handler-case (progn (sel:run (sel:compile-source src) ctx) "no error")
               (sel:sel-error (e) (format nil "~a ~d:~d" (sel:sel-error-code e)
                                          (sel:sel-error-line e) (sel:sel-error-col e))))))
      (say "program.run.over-deep-host-value.key-error-first"
           (format nil "~a|~a" (at "RECORD(TRUE, V)") (at "RECORD(\"k\", V)")))))

  (print-probes)
  (sb-ext:exit :code 0))
