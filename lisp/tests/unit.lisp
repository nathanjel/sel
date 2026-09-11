;;;; Unit tests for the layers underneath the conformance suite.
;;;;
;;;; conformance/ is what proves this implementation correct, and it is
;;;; normative; nothing here duplicates it. These tests cover the internals a
;;;; conformance failure would only point at indirectly — the UTF-8 codec, the
;;;; decimal core, the value model's ordering and dump — plus the public host
;;;; interface, which the conformance runner exercises only in one shape.
;;;;
;;;; Internals are reached with sel:: on purpose: they are not part of the
;;;; interface, and testing them is not an example of use.

(in-package #:sel-tests)

(in-suite sel)

(defun dump-of (source) (sel:value-dump (sel:evaluate source)))

(defmacro raises (code &body body)
  "Checks that BODY signals a sel-error carrying CODE."
  `(handler-case (progn ,@body (fail "expected ~a, nothing was signalled" ,code))
     (sel:sel-error (e) (is (string= ,code (sel:sel-error-code e))))))

(test utf8
  (is (= 0 (length (sel::decode-utf8 (sel::encode-utf8 "")))))
  (is (= 3 (length (sel::decode-utf8 (sel::encode-utf8 "abc")))))
  ;; A CL character is a code point, which is the whole reason no separate code
  ;; point layer is needed in this implementation.
  (is (= 6 (length "Zażółć")))
  (is (= 1 (length "👍")))
  (is (string= "héllo👍" (sel::decode-utf8 (sel::encode-utf8 "héllo👍"))))

  ;; Strict decoding: none of these may become U+FFFD.
  (flet ((bytes (&rest bs) (sel::octets-from-list bs)))
    (raises "E_UTF8" (sel::decode-utf8 (bytes #xc0 #x80)))          ; overlong NUL
    (raises "E_UTF8" (sel::decode-utf8 (bytes #xe0 #x80 #x80)))     ; overlong 3-byte
    (raises "E_UTF8" (sel::decode-utf8 (bytes #xed #xa0 #x80)))     ; CESU-8 surrogate
    (raises "E_UTF8" (sel::decode-utf8 (bytes #xf4 #x90 #x80 #x80))); above U+10FFFF
    (raises "E_UTF8" (sel::decode-utf8 (bytes #xe2 #x82)))          ; truncated
    (raises "E_UTF8" (sel::decode-utf8 (bytes #x80)))               ; lone continuation

    ;; Bytewise, not the host's native order: CL's STRING< is code-point order,
    ;; which disagrees with byte order.
    (is (minusp (sel::bytes-compare (bytes #xef #xbf #xbd) (bytes #xf0 #x9f #x91 #x8d))))
    (is (string= "00ff10" (sel::bytes-to-hex (bytes #x00 #xff #x10))))))

(test ascii-digits
  ;; SBCL's DIGIT-CHAR-P accepts every Unicode decimal digit, so nothing in SEL
  ;; may use it. The differential fuzzer found this the hard way.
  (is (digit-char-p (code-char #x0661)) "the trap still exists in the host")
  (is (not (sel::ascii-digit-p (code-char #x0661))))
  (is (null (sel::dec-parse (string (code-char #x0661)))))
  (is (sel::ascii-digit-p #\7))
  (is (= 15 (sel::ascii-hex-value #\f)))
  (is (null (sel::ascii-hex-value (code-char #x0661)))))

(test decimal
  (flet ((fmt (s) (let ((d (sel::dec-parse s))) (if d (sel::dec-format d) :not-a-number))))
    ;; Canonical form: leading zeros go, trailing fraction zeros stay, zero is
    ;; never negative. Scale is part of the value.
    (is (string= "7" (fmt "007")))
    (is (string= "2.50" (fmt "2.50")))
    (is (string= "0.00" (fmt "-0.00")))
    (is (eq :not-a-number (fmt " 2")))     ; no implicit trimming
    (is (eq :not-a-number (fmt "1.")))
    (is (eq :not-a-number (fmt ".5")))
    (is (eq :not-a-number (fmt "1e3"))))

  (flet ((binop (op a b)
           (sel::dec-format (funcall op (sel::dec-parse a) (sel::dec-parse b)))))
    (is (string= "5.00" (binop #'sel::dec-add "2.50" "2.50")) "money keeps its cents")
    (is (string= "2.25" (binop #'sel::dec-mul "1.5" "1.5")) "* adds the scales")
    (is (string= "0.3" (binop #'sel::dec-add "0.1" "0.2")) "no binary floating point")

    (is (string= "2" (binop #'sel::dec-div "4" "2")) "exact quotients are minimal-scale")
    (is (string= "2.5" (binop #'sel::dec-div "10" "4")))
    (is (string= "0.3333333333" (binop #'sel::dec-div "1" "3")) "inexact runs to DIV_SCALE")
    (is (string= "0.6666666667" (binop #'sel::dec-div "2" "3")) "half away from zero")

    (is (string= "2" (binop #'sel::dec-mod "5" "3")))
    (is (string= "-2" (binop #'sel::dec-mod "-5" "3")) "% takes the dividend's sign")
    (is (string= "1.5" (binop #'sel::dec-mod "5.5" "2")) "% keeps the wider scale")

    (raises "E_DIV_ZERO" (binop #'sel::dec-div "1" "0")))

  (flet ((rnd (a n) (sel::dec-format (sel::dec-round (sel::dec-parse a) n))))
    (is (string= "3" (rnd "2.5" 0)) "half away from zero, up")
    (is (string= "-3" (rnd "-2.5" 0)) "half away from zero, down")
    (is (string= "2" (rnd "2.4" 0)))
    (is (string= "1.00" (rnd "1" 2)) "rounding up in scale pads")))

(test value
  (let ((v (sel:make-none)))
    (sel:value-set v "b" (sel:make-text "1"))
    (sel:value-set v "a" (sel:make-text "2"))
    (is (string= "b" (first (sel:value-keys v))) "insertion order, not sorted order")
    ;; Re-assigning an existing key keeps its original position — order is
    ;; normative and observable.
    (sel:value-set v "b" (sel:make-text "9"))
    (is (string= "b" (first (sel:value-keys v))) "re-assignment does not move a key")
    (is (= 2 (sel:value-size v)) "re-assignment does not add a key"))

  (is (string= "t\"hi\"" (sel:value-dump (sel:make-text "hi"))))
  (is (string= "TRUE" (sel:value-dump (sel:make-bool t))))
  (is (string= "-" (sel:value-dump (sel:make-none))))
  (is (string= "b00ff" (sel:value-dump (sel:make-bin (sel::octets-from-list '(0 255))))))
  (is (string= "t\"a\\nb\\\"c\\\\d\"" (sel:value-dump (sel:make-text "a
b\"c\\d")))
      "the dump escape set")
  (is (string= "t\"\\u0001\"" (sel:value-dump (sel:make-text (string (code-char 1)))))
      "control characters become \\uXXXX")

  ;; EQL is structural: numbers are not normalised.
  (is (not (sel:value-eql (sel:make-text "5.00") (sel:make-text "5"))))
  (is (sel:value-eql (sel:make-text "5") (sel:make-text "5")))

  (let ((a (sel:make-none))
        (b (sel:make-none)))
    (sel:value-set a "1" (sel:make-text "x"))
    (sel:value-set b "2" (sel:make-text "x"))
    (is (not (sel:value-eql a b)) "EQL compares keys, not only values"))

  (is (string= "7" (sel::value-scalar (sel:make-num "007"))) "make-num canonicalises")
  (is (string= "-3" (sel::value-scalar (sel:make-int -3))))
  (raises "E_NOT_NUM" (sel:make-num "x"))

  ;; Scalar context: a value with no scalar takes its first child's, recursively.
  (let ((nested (sel:make-none)))
    (sel:value-set nested "1" (sel:make-text "first"))
    (sel:value-set nested "2" (sel:make-text "second"))
    (is (string= "first" (sel:as-text nested))))
  (raises "E_NULL" (sel:as-text (sel:make-none)))
  (raises "E_NO_SCALAR" (sel:as-text (sel:make-list-value nil)))
  (raises "E_NOT_BOOL" (sel:as-bool (sel:make-text "TRUE"))))

(test host-api
  (is (string= "t\"3\"" (dump-of "1 + 2")))
  (is (string= "-{\"1\"=t\"1\", \"2\"=t\"2\"}" (dump-of "(1, 2)")))
  (is (string= "t\"1\"{\"2\"=t\"x\"}" (dump-of "A = 1; A[2] = \"x\"; A"))
      "a value can have both a scalar and children")

  ;; The context is mutated in place, and host code reads it with the same API
  ;; the interpreter uses.
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "TOTAL" (sel:make-num "59.97"))
    (is (eq t (sel::value-scalar (sel:evaluate "TOTAL > 10.00" ctx))))
    (sel:evaluate "SEEN = TOTAL * 2" ctx)
    (is (string= "119.94" (sel::value-scalar (sel:value-get ctx "SEEN")))))

  (let ((deps (sel:dependencies (sel:compile-source "IF(A > B, A, C)"))))
    (is (equal '("A" "B" "C") deps) "dependencies, sorted"))
  (is (equal '("Y") (sel:dependencies (sel:compile-source "X = 1; X + Y")))
      "an assigned variable is not a dependency")
  (is (equal '("ITEMS") (sel:dependencies (sel:compile-source "ALL(ITEMS, ITEM, ITEM > 0)")))
      "an aggregate binder is not a dependency")

  (raises "E_UNKNOWN_FUNC" (sel:compile-source "NOPE(1)"))
  (raises "E_ARITY" (sel:compile-source "LEN(1, 2)"))
  (raises "E_ARITY" (sel:compile-source "COND(TRUE, 1, FALSE, 2)"))
  (raises "E_SYNTAX" (sel:compile-source "1 < 2 < 3"))
  (raises "E_BAD_ASSIGN" (sel:compile-source "1 = 2"))

  (is (> (length (sel:function-names)) 40))

  ;; Position and code are the contract; the message is not.
  (handler-case (sel:evaluate (format nil "1 +~%  X"))
    (sel:sel-error (e)
      (is (string= "E_UNDEF_VAR" (sel:sel-error-code e)))
      (is (= 2 (sel:sel-error-line e)))
      (is (= 3 (sel:sel-error-col e))))))

(test evaluation-order
  ;; Left-to-right evaluation is observable through which operand's position an
  ;; error reports.
  (flet ((col (source)
           (handler-case (progn (sel:evaluate source) nil)
             (sel:sel-error (e) (sel:sel-error-col e)))))
    (is (= 1 (col "TRUE $== FALSE")) "$== reports the left operand")
    (is (= 1 (col "TRUE + 1")))
    (is (= 5 (col "1 + TRUE")) "the right operand when the left is fine")
    (is (= 1 (col "TRUE < 1")))
    (is (= 10 (col "TRUE XOR 1")))
    (is (= 1 (col "TRUE BAND \"x\""))))

  ;; Short-circuiting means the right side is never reached.
  (is (string= "FALSE" (dump-of "FALSE AND (1/0) EQL TRUE")))
  (is (string= "TRUE" (dump-of "TRUE OR (1/0) EQL TRUE"))))

(test regex-portability
  ;; The cl-ppcre-specific lowering. Perl's `$` also matches before a trailing
  ;; newline; SEL's does not, and neither do the other three hosts.
  (is (string= "FALSE" (dump-of "RMATCH('^a$', \"a\\n\")")))
  (is (string= "TRUE" (dump-of "RMATCH('^a$', \"a\")")))
  ;; Dotall is permanently on.
  (is (string= "TRUE" (dump-of "RMATCH('^a.b$', \"a\\nb\")")))
  ;; Simple case folding reaches the two non-ASCII code points that fold to
  ;; ASCII letters, which cl-ppcre does not do by itself.
  (is (string= "TRUE" (dump-of "RMATCH('^k$', \"\\u{212A}\", \"i\")")))
  (is (string= "TRUE" (dump-of "RMATCH('^s$', \"\\u{017F}\", \"i\")")))
  ;; ...but only simple folding, never full.
  (is (string= "FALSE" (dump-of "RMATCH('^ss$', \"\\u{00DF}\", \"i\")")))
  ;; Groups come back as the subject's own characters, not the folded form. The
  ;; expected code point is spelled out rather than pasted, because U+212A is
  ;; indistinguishable from an ASCII K in a source file — which is the whole
  ;; reason this is worth a test.
  (let ((kelvin (string (code-char #x212a))))
    (is (string= (format nil "-{\"1\"=t\"~a\", \"2\"=t\"~a\"}" kelvin kelvin)
                 (dump-of "RGROUPS('(k)', \"\\u{212A}\", \"i\")"))))
  ;; Non-portable syntax is refused rather than quietly differing.
  (raises "E_REGEX_SYNTAX" (sel:evaluate "RMATCH('\\b', \"x\")"))
  (raises "E_REGEX_SYNTAX" (sel:evaluate "RMATCH('(?=a)', \"a\")")))

;;; The SEL->SQL layer. Everything about it that a case file can state lives in
;;; sql/cases/ and is run by lisp/bin/sqlt against the same corpus as the other
;;; four hosts. These two cannot be stated there: a `.sqlt` case carries its
;;; dialect as a string and runs under the standard printer, so neither the
;;; shape of a host-supplied dialect nor the caller's printer settings is
;;; something the corpus can vary. Both were live defects in this host alone.

(test sql-dialect-need-not-be-a-string
  ;; The dialect arrives from host code, not from a case file, so it can be any
  ;; object at all. Whatever it is, TRY-TRANSLATE must answer NIL: it catches
  ;; SQL-ERROR and nothing else, so a TYPE-ERROR raised deeper in the translator
  ;; would escape the very call whose purpose is to absorb a refusal.
  (let ((p (sel:compile-source "1")))
    (is (null (sel.sql:try-translate p nil)))
    (is (null (sel.sql:try-translate p :mariadb)))    ; a keyword, not "mariadb"
    (is (null (sel.sql:try-translate p 42)))
    (is (null (sel.sql:try-translate p '(1 2))))
    (is (null (sel.sql:try-translate p "nonesuch")))))

(test sql-numbers-are-decimal-whatever-the-caller-prints-in
  ;; *PRINT-BASE* belongs to the caller and this layer may not read it. A number
  ;; the translator decides at translation time -- COUNT over a known list is
  ;; the one that does -- is a SEL number, and SEL numbers are decimal by
  ;; specification (spec/SPEC.md §4). Rendered with PRINC-TO-STRING under the
  ;; binding below, 12 comes out "C", MAKE-NUM raises E_NOT_NUM, and that
  ;; SEL-ERROR escapes TRY-TRANSLATE.
  (let* ((cols (apply #'sel.sql:binding-columns
                      (loop for i from 1 to 12
                            collect (sel.sql:binding-column (format nil "c~D" i)))))
         (bindings (list (cons "COLS" cols)))
         (program (sel:compile-source "COUNT(COLS)")))
    (is (string= "12" (sel.sql:as-value
                       (sel.sql:translate program "mariadb" bindings))))
    (let ((*print-base* 16))
      (is (string= "12" (sel.sql:as-value
                         (sel.sql:translate program "mariadb" bindings)))))
    (let ((*print-base* 2))
      (is (string= "12" (sel.sql:as-value
                         (sel.sql:translate program "mariadb" bindings)))))))

(test sql-builder-receives-the-dialect
  ;; A builder is the map's escape hatch, and the only entry point that hands
  ;; application code anything of this layer's own. What it gets is the dialect:
  ;; Python passes its builder `self.emit` and C++ an `Emit&`, and every emit
  ;; function in this host takes a dialect as its first argument, so the dialect
  ;; IS the emitter here. Handing over the translator instead -- which is what
  ;; this host did -- gives host code an internal structure no other host
  ;; passes, and a builder written against the documented contract breaks.
  ;;
  ;; No .sqlt case can watch this: a case's registrations are JSON and a builder
  ;; is a function, so the corpus cannot express one at all.
  (let ((seen :never-ran))
    (unwind-protect
         (progn
           (sel.sql:define-builder "mariadb" :funcs "UPPER"
             (lambda (emitter args pos)
               (declare (ignore args pos))
               (setf seen emitter)
               (sel.sql::refuse "E_SQL_UNSUPPORTED" "the builder ran" nil)))
           (sel.sql:try-translate (sel:compile-source "UPPER(\"a\")") "mariadb")
           (is (equal "mariadb" seen)))
      (sel.sql:map-reset))))

(test relational-in-memory-operations
  ;; LINK (inner join) with 3 arguments
  (let* ((res (sel:evaluate "A = LIST(RECORD('id', 1, 'name', 'alice'), RECORD('id', 2, 'name', 'bob'));
                             B = LIST(RECORD('user_id', 1, 'role', 'admin'), RECORD('user_id', 3, 'role', 'guest'));
                             LINK(A, B, _1['id'] == _2['user_id'])"))
         (r1 (sel:value-get res "1")))
    (is (= 1 (sel:value-size res)))
    (is (string= "alice" (sel:as-text (sel:value-get r1 "name"))))
    (is (string= "admin" (sel:as-text (sel:value-get r1 "role")))))

  ;; LINK with 5 arguments (custom binders) and collision handling
  (let* ((res (sel:evaluate "A = LIST(RECORD('id', 1, 'val', 10));
                             B = LIST(RECORD('id', 1, 'val', 20));
                             LINK(A, B, a, b, a['id'] == b['id'])"))
         (r1 (sel:value-get res "1")))
    (is (= 1 (sel:value-size res)))
    (is (string= "10" (sel:as-text (sel:value-get (sel:value-get r1 "a") "val"))))
    (is (string= "20" (sel:as-text (sel:value-get (sel:value-get r1 "b") "val"))))
    ;; Ambiguous field collision: "val" was not promoted to top-level
    (is (null (sel:value-get r1 "val"))))

  ;; LINK_LEFT (left outer join)
  (let* ((res (sel:evaluate "A = LIST(RECORD('id', 1, 'name', 'alice'), RECORD('id', 2, 'name', 'bob'));
                             B = LIST(RECORD('user_id', 1, 'role', 'admin'));
                             LINK_LEFT(A, B, _1['id'] == _2['user_id'])"))
         (r1 (sel:value-get res "1"))
         (r2 (sel:value-get res "2")))
    (is (= 2 (sel:value-size res)))
    (is (string= "alice" (sel:as-text (sel:value-get r1 "name"))))
    (is (string= "admin" (sel:as-text (sel:value-get r1 "role"))))
    (is (string= "bob" (sel:as-text (sel:value-get r2 "name"))))
    ;; Unmatched row role is null/none
    (is (null (sel:value-get r2 "role"))))

  ;; BUCKET with _K and aggregates
  (let* ((res (sel:evaluate "BUCKET(LIST(RECORD('k', 'a', 'v', 10), RECORD('k', 'a', 'v', 20), RECORD('k', 'b', 'v', 30)),
                                    _['k'],
                                    RECORD('k', _K, 'tot', SUM(_, _['v']), 'cnt', COUNT(_)))"))
         (r1 (sel:value-get res "1"))
         (r2 (sel:value-get res "2")))
    (is (= 2 (sel:value-size res)))
    (is (string= "30" (sel:as-text (sel:value-get r1 "tot"))))
    (is (string= "2" (sel:as-text (sel:value-get r1 "cnt"))))
    (is (string= "30" (sel:as-text (sel:value-get r2 "tot"))))
    (is (string= "1" (sel:as-text (sel:value-get r2 "cnt")))))

  ;; DEDUPE
  (let ((res (sel:evaluate "DEDUPE(LIST(1, 2, 2, 3, 1, 4))")))
    (is (string= "-{\"1\"=t\"1\", \"2\"=t\"2\", \"3\"=t\"3\", \"4\"=t\"4\"}" (sel:value-dump res)))))

(test relational-sql-joins
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "C_ID" (sel.sql:binding-column "c_id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (customers (sel.sql:binding-relation "customers" "customers"
                      (list (cons "ID" (sel.sql:binding-column "id" "customers"))
                            (cons "NAME" (sel.sql:binding-column "name" "customers")))))
         (items (sel.sql:binding-relation "items" "items"
                  (list (cons "ITEM_ID" (sel.sql:binding-column "item_id" "items"))
                        (cons "ORDER_ID" (sel.sql:binding-column "order_id" "items"))
                        (cons "PRICE" (sel.sql:binding-column "price" "items")))))
         (bindings (list (cons "ORDERS" orders)
                         (cons "CUSTOMERS" customers)
                         (cons "ITEMS" items))))
    ;; Basic LINK
    (let* ((p (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _['c_id'] == _2['id'])"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "INNER JOIN \"customers\" \"customers\" ON" sql))))
      (is (not (null (search "\"orders\".\"c_id\"" sql))))
      (is (not (null (search "\"customers\".\"id\"" sql)))))

    ;; LINK_LEFT
    (let* ((p (sel:compile-source "ORDERS .> LINK_LEFT(CUSTOMERS, _['c_id'] == _2['id'])"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "LEFT JOIN \"customers\" \"customers\" ON" sql)))))

    ;; Ambiguous column rejection
    (let ((p (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _['c_id'] == _2['id']) .> FILTER(_['id'] == 1)")))
      (handler-case (sel.sql:translate-statement p "postgresql" bindings)
        (sel.sql:sql-error (e)
          (is (string= "E_SQL_SHAPE" (sel.sql:sql-error-code e))))
        (:no-error (val)
          (declare (ignore val))
          (fail "Expected E_SQL_SHAPE for ambiguous column"))))

    ;; Disambiguated access via table qualifier
    (let* ((p (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _['c_id'] == _2['id']) .> FILTER(_['orders']['id'] == 1)"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "\"orders\".\"id\"" sql)))))

    ;; Multi-table join
    (let* ((p (sel:compile-source "ORDERS .> LINK(CUSTOMERS, _['c_id'] == _2['id']) .> LINK(ITEMS, _['orders']['id'] == _2['order_id'])"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "INNER JOIN \"customers\"" sql))))
      (is (not (null (search "INNER JOIN \"items\"" sql)))))))

(test relational-sql-derived-tables
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "C_ID" (sel.sql:binding-column "c_id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (bindings (list (cons "ORDERS" orders))))
    ;; MAP computed column followed by FILTER requires derived table subquery
    (let* ((p (sel:compile-source "ORDERS .> MAP(RECORD('id', _['id'], 'double_amt', _['amount'] * 2)) .> FILTER(_['double_amt'] > 100)"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "FROM (SELECT \"orders\".\"id\" AS \"id\"" sql))))
      (is (not (null (search "\"_sub1\" WHERE" sql))))
      (is (not (null (search "\"_sub1\".\"double_amt\"" sql)))))

    ;; TAKE followed by FILTER requires subquery
    (let* ((p (sel:compile-source "ORDERS .> TAKE(10) .> FILTER(_['amount'] > 50)"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "LIMIT 10) \"_sub1\" WHERE" sql))))
      (is (not (null (search "\"_sub1\".\"amount\"" sql)))))

    ;; Multi-stage derived table: BUCKET then MAP then FILTER
    (let* ((p (sel:compile-source "ORDERS .> BUCKET(_['c_id'], RECORD('c_id', _K, 'total', SUM(_, _['amount']))) .> MAP(RECORD('c_id', _['c_id'], 'tax', _['total'] * 0.2)) .> FILTER(_['tax'] > 20)"))
           (frag (sel.sql:translate-statement p "postgresql" bindings))
           (sql (sel.sql:as-statement frag)))
      (is (not (null (search "\"_sub1\"" sql))))
      (is (not (null (search "\"_sub2\"" sql))))
      (is (not (null (search "GROUP BY \"orders\".\"c_id\"" sql))))
      (is (not (null (search "\"_sub2\".\"tax\"" sql)))))))

(test hybrid-execution-planner
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "C_ID" (sel.sql:binding-column "c_id" "orders"))
                         (cons "AMOUNT" (sel.sql:binding-column "amount" "orders")))))
         (bindings (list (cons "ORDERS" orders))))
    ;; Pure SQL plan
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> SORT_BY(_['id'])"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
      (is-true (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
      (is (null (sel.sql:hybrid-plan-continuation-program plan)))
      (let* ((runner-called nil)
             (mock-runner (lambda (sql params)
                            (declare (ignore params))
                            (setf runner-called t)
                            (is (not (null (search "WHERE" sql))))
                            (sel:evaluate "LIST(RECORD('id', 1, 'amount', 120))"))))
        (let ((res (sel.sql:execute-hybrid plan mock-runner)))
          (is-true runner-called)
          (is (= 1 (sel:value-size res))))))

    ;; Split hybrid plan (pushdown prefix + in-memory continuation)
    (let* ((p (sel:compile-source "ORDERS .> FILTER(_['amount'] > 100) .> MAP(RECORD('id', _['id'], 'groups', RGROUPS('([0-9]+)', _['id'])))"))
           (plan (sel.sql:plan-hybrid p "postgresql" bindings)))
      (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
      (is (not (null (sel.sql:hybrid-plan-sql-statement plan))))
      (is (not (null (sel.sql:hybrid-plan-continuation-program plan))))
      (let* ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan)))
             (mock-runner (lambda (query params)
                            (declare (ignore query params))
                            (sel:evaluate "LIST(RECORD('id', 'vip-42', 'amount', 150), RECORD('id', 'reg-99', 'amount', 200))")))
             (res (sel.sql:execute-hybrid plan mock-runner)))
        (is (not (null (search "WHERE" sql))))
        (is (= 2 (sel:value-size res)))
        (is (string= "42" (sel:as-text (sel:value-get (sel:value-get (sel:value-get res "1") "groups") "1"))))
        (is (string= "99" (sel:as-text (sel:value-get (sel:value-get (sel:value-get res "2") "groups") "1"))))))

    ;; Pure memory plan
    (let* ((p (sel:compile-source "LOCAL_ROWS .> FILTER(_['x'] > 1)"))
           (plan (sel.sql:plan-hybrid p "postgresql" nil))
           (ctx (sel:make-none)))
      (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
      (is-true (sel.sql:hybrid-plan-pure-memory-p plan))
      (sel:value-set ctx "LOCAL_ROWS" (sel:evaluate "LIST(RECORD('x', 1), RECORD('x', 2))"))
      (let ((res (sel.sql:execute-hybrid plan nil ctx)))
        (is (= 1 (sel:value-size res)))
        (is (string= "2" (sel:as-text (sel:value-get (sel:value-get res "2") "x"))))))))

(test relational-complex-twisted-pipeline
  ;; 1. In-memory execution of the full 9-stage pipeline
  (let* ((code "ORDERS = LIST(
                  RECORD('id', 1, 'status', 'COMPLETED'),
                  RECORD('id', 2, 'status', 'PENDING'),
                  RECORD('id', 3, 'status', 'COMPLETED')
                );
                LINE_ITEMS = LIST(
                  RECORD('order_id', 1, 'category', 'ELECTRONICS', 'unit_price', 100, 'qty', 2),
                  RECORD('order_id', 1, 'category', 'ELECTRONICS', 'unit_price', 10, 'qty', 1),
                  RECORD('order_id', 2, 'category', 'BOOKS', 'unit_price', 200, 'qty', 1),
                  RECORD('order_id', 3, 'category', 'BOOKS', 'unit_price', 120, 'qty', 2)
                );
                ORDERS .> FILTER(_['status'] $== 'COMPLETED')
                       .> LINK(LINE_ITEMS, _1['id'] == _2['order_id'])
                       .> MAP(RECORD('cat', _['category'], 'line_total', _['unit_price'] * _['qty']))
                       .> FILTER(_['line_total'] > 50)
                       .> BUCKET(_['cat'], RECORD('cat', _K, 'spend', SUM(_, _['line_total']), 'lines', COUNT(_)))
                       .> FILTER(_['spend'] >= 200)
                       .> SORT_BY(_['spend'], 'DESC')
                       .> TAKE(5)
                       .> MAP(RECORD('category', _['cat'], 'gross_spend', _['spend'], 'tag', RGROUPS('^([A-Z]+)', _['cat'])[1]))")
         (res (sel:evaluate code)))
    (is (= 2 (sel:value-size res)))
    (let ((row1 (sel:value-get res "1"))
          (row2 (sel:value-get res "2")))
      (is (string= "BOOKS" (sel:as-text (sel:value-get row1 "category"))))
      (is (string= "240" (sel:as-text (sel:value-get row1 "gross_spend"))))
      (is (string= "BOOKS" (sel:as-text (sel:value-get row1 "tag"))))
      (is (string= "ELECTRONICS" (sel:as-text (sel:value-get row2 "category"))))
      (is (string= "200" (sel:as-text (sel:value-get row2 "gross_spend"))))
      (is (string= "ELECTRONICS" (sel:as-text (sel:value-get row2 "tag"))))))

  ;; 2. Relational plan compilation & hybrid execution
  (let* ((orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders"))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "orders"))
                         (cons "STATUS" (sel.sql:binding-column "status" "orders")))))
         (items (sel.sql:binding-relation "line_items" "line_items"
                  (list (cons "ORDER_ID" (sel.sql:binding-column "order_id" "line_items"))
                        (cons "CATEGORY" (sel.sql:binding-column "category" "line_items"))
                        (cons "UNIT_PRICE" (sel.sql:binding-column "unit_price" "line_items"))
                        (cons "QTY" (sel.sql:binding-column "qty" "line_items")))))
         (bindings (list (cons "ORDERS" orders) (cons "LINE_ITEMS" items)))
         (query-code "ORDERS .> FILTER(_['status'] $== 'COMPLETED')
                             .> LINK(LINE_ITEMS, _['orders']['id'] == _2['order_id'])
                             .> MAP(RECORD('cat', _['category'], 'line_total', _['unit_price'] * _['qty']))
                             .> FILTER(_['line_total'] > 50)
                             .> BUCKET(_['cat'], RECORD('cat', _K, 'spend', SUM(_, _['line_total']), 'lines', COUNT(_)))
                             .> FILTER(_['spend'] >= 200)
                             .> SORT_BY(_['spend'], 'DESC')
                             .> TAKE(5)
                             .> MAP(RECORD('category', _['cat'], 'gross_spend', _['spend'], 'tag', RGROUPS('^([A-Z]+)', _['cat'])[1]))")
         (prog (sel:compile-source query-code))
         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
    (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
    (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
    (let ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))))
      (is (not (null (search "INNER JOIN \"line_items\"" sql))))
      (is (not (null (search "\"orders\".\"status\"" sql))))
      (is (not (null (search "\"_sub1\"" sql))))
      (is (not (null (search "GROUP BY \"_sub1\".\"cat\"" sql))))
      (is (not (null (search "HAVING (COALESCE(SUM(\"_sub1\".\"line_total\"), 0) >= 200)" sql))))
      (is (not (null (search "ORDER BY COALESCE(SUM(\"_sub1\".\"line_total\"), 0) DESC" sql))))
      (is (not (null (search "LIMIT 5" sql)))))
    ;; Verify hybrid execution
    (let* ((mock-db (lambda (sql params)
                      (declare (ignore sql params))
                      (sel:evaluate "LIST(RECORD('cat', 'BOOKS', 'spend', 240, 'lines', 2),
                                          RECORD('cat', 'ELECTRONICS', 'spend', 200, 'lines', 1))")))
           (res (sel.sql:execute-hybrid plan mock-db)))
      (is (= 2 (sel:value-size res)))
      (let ((row1 (sel:value-get res "1"))
            (row2 (sel:value-get res "2")))
        (is (string= "BOOKS" (sel:as-text (sel:value-get row1 "category"))))
        (is (string= "240" (sel:as-text (sel:value-get row1 "gross_spend"))))
        (is (string= "BOOKS" (sel:as-text (sel:value-get row1 "tag"))))
        (is (string= "ELECTRONICS" (sel:as-text (sel:value-get row2 "category"))))
        (is (string= "200" (sel:as-text (sel:value-get row2 "gross_spend"))))
        (is (string= "ELECTRONICS" (sel:as-text (sel:value-get row2 "tag"))))))))

(test fallthrough-custom-function
  (sel:register-builtin "TEST_VIP_SCORE" 2 2
    (lambda (a ctx)
      (declare (ignore ctx))
      (let* ((tier (sel:as-text (sel::args-val a 0)))
             (year (or (ignore-errors (parse-integer (sel:as-text (sel::args-val a 1)))) 2024))
             (base (if (string= tier "GOLD") 50 10)))
        (sel:make-num (format nil "~D" (+ base (* (- 2026 year) 5)))))))
  (let* ((cust (sel.sql:binding-relation "customers" "customers"
                 (list (cons "ID" (sel.sql:binding-column "id" "customers" :num))
                       (cons "TIER" (sel.sql:binding-column "tier" "customers" :text))
                       (cons "COUNTRY" (sel.sql:binding-column "country" "customers" :text))
                       (cons "CREATED_YEAR" (sel.sql:binding-column "created_year" "customers" :num)))))
         (bindings (list (cons "CUSTOMERS" cust)))
         (q "CUSTOMERS .> MAP(RECORD('id', _['id'], 'country', _['country'], 'score', TEST_VIP_SCORE(_['tier'], _['created_year'])))
                       .> FILTER(_['country'] $== 'DE')
                       .> SORT_BY(_['id'], 'ASC')
                       .> TAKE(3)")
         (prog (sel:compile-source q))
         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
    (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
    (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
    (let ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))))
      ;; Verify SQL pushed down the WHERE, ORDER BY, and LIMIT, and passed through tier and created_year
      (is (not (null (search "\"tier\"" sql))))
      (is (not (null (search "\"created_year\"" sql))))
      (is (not (null (search "WHERE" sql))))
      (is (not (null (search "ORDER BY" sql))))
      (is (not (null (search "LIMIT 3" sql)))))
    ;; Verify execution through hybrid executor
    (let* ((mock-db (lambda (sql params)
                      (declare (ignore sql params))
                      (sel:evaluate "LIST(RECORD('id', 1, 'country', 'DE', 'tier', 'GOLD', 'created_year', 2021),
                                          RECORD('id', 5, 'country', 'DE', 'tier', 'BRONZE', 'created_year', 2024))")))
           (res (sel.sql:execute-hybrid plan mock-db)))
      (is (= 2 (sel:value-size res)))
      (let ((r1 (sel:value-get res "1"))
            (r2 (sel:value-get res "2")))
        (is (string= "1" (sel:as-text (sel:value-get r1 "id"))))
        (is (string= "DE" (sel:as-text (sel:value-get r1 "country"))))
        (is (string= "75" (sel:as-text (sel:value-get r1 "score"))))
        (is (string= "5" (sel:as-text (sel:value-get r2 "id"))))
        (is (string= "20" (sel:as-text (sel:value-get r2 "score"))))))))

(test mid-pipeline-memory-fallback
  (sel:register-builtin "TEST_RISK_SCORE" 2 2
    (lambda (a ctx)
      (declare (ignore ctx))
      (let* ((country (sel:as-text (sel::args-val a 0)))
             (disc (or (ignore-errors (parse-integer (sel:as-text (sel::args-val a 1)))) 0))
             (base (if (string= country "US") 30 10)))
        (sel:make-num (format nil "~D" (+ base (* disc 2)))))))
  (let* ((cust (sel.sql:binding-relation "customers" "customers"
                 (list (cons "ID" (sel.sql:binding-column "id" "customers" :num))
                       (cons "COUNTRY" (sel.sql:binding-column "country" "customers" :text)))))
         (orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders" :num))
                         (cons "CUSTOMER_ID" (sel.sql:binding-column "customer_id" "orders" :num))
                         (cons "STATUS" (sel.sql:binding-column "status" "orders" :text))
                         (cons "DISCOUNT" (sel.sql:binding-column "discount" "orders" :num)))))
         (bindings (list (cons "CUSTOMERS" cust) (cons "ORDERS" orders)))
         (q "ORDERS .> LINK(CUSTOMERS, _['customer_id'] == _2['id'])
                    .> FILTER(_['orders']['status'] $== 'COMPLETED')
                    .> MAP(RECORD('cust_id', _['customers']['id'],
                                  'country', _['customers']['country'],
                                  'discount', _['orders']['discount']))
                    .> MAP(RECORD('cust_id', _['cust_id'],
                                  'country', _['country'],
                                  'score', TEST_RISK_SCORE(_['country'], _['discount'])))
                    .> FILTER(_['score'] > 50)
                    .> BUCKET(_['country'], RECORD('country', _K, 'cnt', COUNT(_)))
                    .> SORT_BY(_['cnt'], 'DESC')
                    .> TAKE(2)")
         (prog (sel:compile-source q))
         (plan (sel.sql:plan-hybrid prog "postgresql" bindings)))
    (is-false (sel.sql:hybrid-plan-pure-sql-p plan))
    (is-false (sel.sql:hybrid-plan-pure-memory-p plan))
    (let ((sql (sel.sql:as-statement (sel.sql:hybrid-plan-sql-statement plan))))
      (is (not (null (search "INNER JOIN \"customers\"" sql))))
      (is (not (null (search "\"orders\".\"status\"" sql)))))
    ;; Continuation executes MAP with custom score, FILTER, BUCKET, SORT_BY, and TAKE
    (let* ((mock-db (lambda (sql params)
                      (declare (ignore sql params))
                      (sel:evaluate "LIST(RECORD('cust_id', 1, 'country', 'US', 'discount', 20),
                                          RECORD('cust_id', 2, 'country', 'US', 'discount', 5),
                                          RECORD('cust_id', 3, 'country', 'DE', 'discount', 25),
                                          RECORD('cust_id', 4, 'country', 'DE', 'discount', 10))")))
           (res (sel.sql:execute-hybrid plan mock-db)))
      (is (= 2 (sel:value-size res)))
      (let ((r1 (sel:value-get res "1"))
            (r2 (sel:value-get res "2")))
        ;; US: disc 20 -> 30 + 40 = 70 (>50: 1 row); disc 5 -> 30 + 10 = 40 (<=50)
        ;; DE: disc 25 -> 10 + 50 = 60 (>50: 1 row); disc 10 -> 10 + 20 = 30 (<=50)
        (is (= 2 (sel:value-size res)))
        (is-true (or (string= "US" (sel:as-text (sel:value-get r1 "country")))
                     (string= "DE" (sel:as-text (sel:value-get r1 "country")))))))))

(test deep-14-stage-pipeline
  (let* ((cats (sel.sql:binding-relation "categories" "categories"
                 (list (cons "ID" (sel.sql:binding-column "id" "categories" :num))
                       (cons "NAME" (sel.sql:binding-column "name" "categories" :text)))))
         (prods (sel.sql:binding-relation "products" "products"
                  (list (cons "ID" (sel.sql:binding-column "id" "products" :num))
                        (cons "CAT_ID" (sel.sql:binding-column "cat_id" "products" :num))
                        (cons "PRICE" (sel.sql:binding-column "price" "products" :num))
                        (cons "IS_ACTIVE" (sel.sql:binding-column "is_active" "products" :num)))))
         (orders (sel.sql:binding-relation "orders" "orders"
                   (list (cons "ID" (sel.sql:binding-column "id" "orders" :num))
                         (cons "CUST_ID" (sel.sql:binding-column "cust_id" "orders" :num))
                         (cons "STATUS" (sel.sql:binding-column "status" "orders" :text))
                         (cons "DISCOUNT" (sel.sql:binding-column "discount" "orders" :num))
                         (cons "YEAR" (sel.sql:binding-column "year" "orders" :num)))))
         (items (sel.sql:binding-relation "order_items" "order_items"
                  (list (cons "ID" (sel.sql:binding-column "id" "order_items" :num))
                        (cons "ORDER_ID" (sel.sql:binding-column "order_id" "order_items" :num))
                        (cons "PROD_ID" (sel.sql:binding-column "prod_id" "order_items" :num))
                        (cons "QTY" (sel.sql:binding-column "qty" "order_items" :num))
                        (cons "PRICE" (sel.sql:binding-column "price" "order_items" :num)))))
         (bindings (list (cons "CATEGORIES" cats)
                         (cons "PRODUCTS" prods)
                         (cons "ORDERS" orders)
                         (cons "ORDER_ITEMS" items)))
         (q "ORDERS .> FILTER(_['status'] $== 'COMPLETED')
                    .> FILTER(_['year'] >= 2025)
                    .> MAP(RECORD('order_id', _['id'], 'discount', _['discount']))
                    .> LINK(ORDER_ITEMS, _['_sub1']['order_id'] == _2['order_id'])
                    .> LINK(PRODUCTS, _['order_items']['prod_id'] == _2['id'])
                    .> FILTER(_['products']['is_active'] == 1)
                    .> MAP(RECORD('order_id', _['order_items']['order_id'],
                                  'cat_id', _['products']['cat_id'],
                                  'line_net', (_['order_items']['price'] * _['order_items']['qty']) - _['_sub1']['discount']))
                    .> FILTER(_['line_net'] > 10)
                    .> LINK(CATEGORIES, _['_sub2']['cat_id'] == _2['id'])
                    .> BUCKET(_['categories']['name'],
                              RECORD('cat_name', _K,
                                     'lines', COUNT(_),
                                     'total_net', SUM(_, _['line_net']),
                                     'max_net', MAX(_, _['line_net'])))
                    .> FILTER(_['total_net'] >= 500)
                    .> SORT_BY(_['total_net'], 'DESC')
                    .> TAKE(10)")
         (prog (sel:compile-source q))
         (stmt-pg (sel.sql:translate-statement prog "postgresql" bindings))
         (stmt-ma (sel.sql:translate-statement prog "mariadb" bindings)))

    (is (not (null stmt-pg)))
    (is (not (null stmt-ma)))
    (let ((sql-pg (sel.sql:as-statement stmt-pg))
          (sql-ma (sel.sql:as-statement stmt-ma)))
      (is (not (null (search "\"_sub1\"" sql-pg))))
      (is (not (null (search "`_sub1`" sql-ma))))
      (is (not (null (search "HAVING" sql-pg))))
      (is (not (null (search "HAVING" sql-ma))))
      (is (not (null (search "LIMIT 10" sql-pg))))
      (is (not (null (search "LIMIT 10" sql-ma)))))))

(test in-memory-top-n
  ;; TOP and TOP_DESC on flat lists
  (let ((res1 (sel:evaluate "LIST(5, 1, 9, 3, 7) .> TOP(3)"))
        (res2 (sel:evaluate "LIST(5, 1, 9, 3, 7) .> TOP_DESC(2)"))
        (res3 (sel:evaluate "LIST(5, 1, 9, 3, 7) .> TOP(0)"))
        (res4 (sel:evaluate "LIST(5, 1, 9, 3, 7) .> TOP(10)")))
    (is (string= "t\"1\"" (sel:value-dump (sel:value-get res1 "1"))))
    (is (string= "t\"3\"" (sel:value-dump (sel:value-get res1 "2"))))
    (is (string= "t\"5\"" (sel:value-dump (sel:value-get res1 "3"))))
    (is (= 3 (sel:value-size res1)))

    (is (string= "t\"9\"" (sel:value-dump (sel:value-get res2 "1"))))
    (is (string= "t\"7\"" (sel:value-dump (sel:value-get res2 "2"))))
    (is (= 2 (sel:value-size res2)))

    (is (= 0 (sel:value-size res3)))
    (is (= 5 (sel:value-size res4))))

  ;; TOP_BY on records with default ASC and explicit DESC
  (let ((data "LIST(RECORD('id', 1, 'val', 50),
                    RECORD('id', 2, 'val', 10),
                    RECORD('id', 3, 'val', 90),
                    RECORD('id', 4, 'val', 30))"))
    (let ((top2 (sel:evaluate (format nil "~a .> TOP_BY(_['val'], 2)" data)))
          (top-desc2 (sel:evaluate (format nil "~a .> TOP_BY(r, r['val'], 'DESC', 2)" data))))
      (is (= 2 (sel:value-size top2)))
      (is (string= "10" (sel:as-text (sel:value-get (sel:value-get top2 "1") "val"))))
      (is (string= "30" (sel:as-text (sel:value-get (sel:value-get top2 "2") "val"))))

      (is (= 2 (sel:value-size top-desc2)))
      (is (string= "90" (sel:as-text (sel:value-get (sel:value-get top-desc2 "1") "val"))))
      (is (string= "50" (sel:as-text (sel:value-get (sel:value-get top-desc2 "2") "val")))))))

(test lazy-record-evaluation
  (let ((eval-count 0))
    (sel:register-builtin "TEST_HEAVY_OP" 1 1
      (lambda (a ctx)
        (declare (ignore ctx))
        (incf eval-count)
        (sel:make-num (format nil "~d" (* (parse-integer (sel:as-text (sel::args-val a 0))) 10)))))
    ;; Generate 10 records, map with LAZY_RECORD, take 2.
    ;; Only 2 rows should ever trigger TEST_HEAVY_OP.
    (let* ((q "LIST(RECORD('id', 1), RECORD('id', 2), RECORD('id', 3), RECORD('id', 4), RECORD('id', 5))
              .> MAP(r, LAZY_RECORD('id', r['id'], 'heavy', TEST_HEAVY_OP(r['id'])))
              .> TAKE(2)")
           (res (sel:evaluate q)))
      (is (= 0 eval-count)) ;; No field accessed yet!
      (is (= 2 (sel:value-size res)))
      (is (= 0 eval-count)) ;; value-size doesn't access fields!
      (let ((row1 (sel:value-get res "1")))
        ;; Access only 'id', 'heavy' still not evaluated!
        (is (string= "1" (sel:as-text (sel:value-get row1 "id"))))
        (is (= 0 eval-count))
        ;; Now access 'heavy' on row 1
        (is (string= "10" (sel:as-text (sel:value-get row1 "heavy"))))
        (is (= 1 eval-count))
        ;; Access again, thunk is memoized!
        (is (string= "10" (sel:as-text (sel:value-get row1 "heavy"))))
        (is (= 1 eval-count))))))

(test ast-pipeline-optimizer
  ;; 1. SORT_BY + TAKE -> TOP_BY
  (let* ((prog (sel:compile-source "DATA .> SORT_BY(_['x'], 'DESC') .> TAKE(5)"))
         (opt-ast (sel:optimize-ast (sel:program-ast prog))))
    (is (string= "TOP_BY" (sel::node-s opt-ast)))
    (is (= 4 (length (sel::node-items opt-ast)))))

  ;; 2. Filter Pushdown: MAP .> FILTER where FILTER only uses pass-through fields
  (let* ((prog (sel:compile-source "DATA .> MAP(RECORD('id', _['id'], 'heavy', _['x'] * 2)) .> FILTER(_['id'] > 10)"))
         (opt-ast (sel:optimize-ast (sel:program-ast prog))))
    ;; Top-level should now be MAP, with child FILTER
    (is (string= "MAP" (sel::node-s opt-ast)))
    (let ((child (first (sel::node-items opt-ast))))
      (is (string= "FILTER" (sel::node-s child)))))

  ;; 3. Late Materialization: MAP .> TOP_BY
  (let* ((prog (sel:compile-source "DATA .> MAP(RECORD('id', _['id'], 'heavy', _['x'] * 2)) .> TOP_BY(_['id'], 3)"))
         (opt-ast (sel:optimize-ast (sel:program-ast prog))))
    ;; Top-level should now be MAP, with child TOP_BY
    (is (string= "MAP" (sel::node-s opt-ast)))
    (let ((child (first (sel::node-items opt-ast))))
      (is (string= "TOP_BY" (sel::node-s child)))))

  ;; 4. End-to-end execution equivalence & optimization verification
  (let ((calc-count 0))
    (sel:register-builtin "EXPENSIVE_FUNC" 1 1
      (lambda (a ctx)
        (declare (ignore ctx))
        (incf calc-count)
        (sel:make-num (format nil "~d" (* (parse-integer (sel:as-text (sel::args-val a 0))) 100)))))
    (let* ((data (sel:evaluate "LIST(RECORD('id', 1, 'score', 50),
                                     RECORD('id', 2, 'score', 20),
                                     RECORD('id', 3, 'score', 80),
                                     RECORD('id', 4, 'score', 10),
                                     RECORD('id', 5, 'score', 95))"))
           (q "DATA .> MAP(RECORD('id', _['id'], 'score', _['score'], 'bonus', EXPENSIVE_FUNC(_['score'])))
                    .> FILTER(_['score'] >= 50)
                    .> SORT_BY(_['score'], 'DESC')
                    .> TAKE(2)")
           (ctx (sel:make-none)))
      (sel:value-set ctx "DATA" data)
      (let ((res (sel:evaluate q ctx)))
        (is (= 2 (sel:value-size res)))
        (let ((r1 (sel:value-get res "1"))
              (r2 (sel:value-get res "2")))
          (is (string= "5" (sel:as-text (sel:value-get r1 "id"))))
          (is (string= "95" (sel:as-text (sel:value-get r1 "score"))))
          (is (string= "9500" (sel:as-text (sel:value-get r1 "bonus"))))
          (is (string= "3" (sel:as-text (sel:value-get r2 "id"))))
          (is (string= "80" (sel:as-text (sel:value-get r2 "score"))))
          (is (string= "8000" (sel:as-text (sel:value-get r2 "bonus"))))
          ;; Because of filter pushdown, late materialization, and lazy records,
          ;; EXPENSIVE_FUNC was only executed for the 2 rows that were actually accessed!
          (is (= 2 calc-count)))))

  ;; 5. Slicing Fusion: TAKE(10) .> TAKE(5) -> TAKE(5), DROP(10) .> DROP(5) -> DROP(15)
  (let* ((prog1 (sel:compile-source "DATA .> TAKE(10) .> TAKE(5)"))
         (opt1 (sel:optimize-ast-logical (sel:program-ast prog1)))
         (prog2 (sel:compile-source "DATA .> DROP(10) .> DROP(5)"))
         (opt2 (sel:optimize-ast-logical (sel:program-ast prog2))))
    (is (string= "TAKE" (sel::node-s opt1)))
    (is (string= "5" (sel::node-s (second (sel::node-items opt1)))))
    (is (string= "DROP" (sel::node-s opt2)))
    (is (string= "15" (sel::node-s (second (sel::node-items opt2))))))

  ;; 6. Filter Pushdown through SORT_BY
  (let* ((prog (sel:compile-source "DATA .> SORT_BY(_['x']) .> FILTER(_['y'] > 10)"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    ;; Top-level should be SORT_BY, with child FILTER
    (is (string= "SORT_BY" (sel::node-s opt)))
    (let ((child (first (sel::node-items opt))))
      (is (string= "FILTER" (sel::node-s child)))))

  ;; 7. Filter Pushdown through SELECT_COLS
  (let* ((prog (sel:compile-source "DATA .> SELECT_COLS('id', 'y') .> FILTER(_['id'] > 10)"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    ;; Top-level should be SELECT_COLS, with child FILTER
    (is (string= "SELECT_COLS" (sel::node-s opt)))
    (let ((child (first (sel::node-items opt))))
      (is (string= "FILTER" (sel::node-s child)))))

  ;; 8. Tier 1 Logical vs Tier 2 In-Memory distinction
  (let* ((prog (sel:compile-source "DATA .> MAP(RECORD('id', _['id'], 'x', _['val'] * 2))"))
         (opt-logical (sel:optimize-ast-logical (sel:program-ast prog)))
         (opt-memory (sel:optimize-ast-in-memory (sel:program-ast prog))))
    ;; Logical optimizer keeps RECORD
    (let ((rec-log (second (sel::node-items opt-logical))))
      (is (string= "RECORD" (sel::node-s rec-log))))
    ;; In-memory optimizer rewrites RECORD to LAZY_RECORD
    (let ((rec-mem (second (sel::node-items opt-memory))))
      (is (string= "LAZY_RECORD" (sel::node-s rec-mem))))))

  ;; 9. Constant Folding and Dead Branch Elimination
  (let* ((prog1 (sel:compile-source "(10 + 20) * 3 - 5"))
         (opt1 (sel:optimize-ast-logical (sel:program-ast prog1))))
    (is (eq :num (sel::node-kind opt1)))
    (is (string= "85" (sel::node-s opt1))))

  (let* ((prog2 (sel:compile-source "FALSE AND (x > 100)"))
         (opt2 (sel:optimize-ast-logical (sel:program-ast prog2))))
    (is (eq :bool (sel::node-kind opt2)))
    (is-false (sel::node-b opt2)))

  (let* ((prog3 (sel:compile-source "TRUE OR (x > 100)"))
         (opt3 (sel:optimize-ast-logical (sel:program-ast prog3))))
    (is (eq :bool (sel::node-kind opt3)))
    (is-true (sel::node-b opt3)))

  (let* ((prog4 (sel:compile-source "IF(10 > 5, 42, 99)"))
         (opt4 (sel:optimize-ast-logical (sel:program-ast prog4))))
    (is (eq :num (sel::node-kind opt4)))
    (is (string= "42" (sel::node-s opt4))))

  (let* ((prog5 (sel:compile-source "IF(FALSE, 42, 99)"))
         (opt5 (sel:optimize-ast-logical (sel:program-ast prog5))))
    (is (eq :num (sel::node-kind opt5)))
    (is (string= "99" (sel::node-s opt5))))

  (let ((v (sel:evaluate "IF(2 + 2 == 4, 'yes', 'no')")))
    (is (string= "yes" (sel:as-text v))))

  ;; 10. Consecutive FILTER + FILTER Fusion -> Single FILTER(p1 AND p2)
  (let* ((prog (sel:compile-source "DATA .> FILTER(_['x'] > 10) .> FILTER(_['y'] < 20)"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    (is (string= "FILTER" (sel::node-s opt)))
    (is (string= "DATA" (sel::node-s (first (sel::node-items opt)))))
    (let ((pred (second (sel::node-items opt))))
      (is (eq :bin (sel::node-kind pred)))
      (is (string= "AND" (sel::node-s pred)))))

  ;; 11. Redundant successive SORT elimination
  (let* ((prog (sel:compile-source "DATA .> SORT_BY(_['a']) .> SORT_BY(_['b'])"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    (is (string= "SORT_BY" (sel::node-s opt)))
    (is (string= "DATA" (sel::node-s (first (sel::node-items opt))))))

  ;; 12. Redundant successive DEDUPE elimination
  (let* ((prog (sel:compile-source "DATA .> DEDUPE() .> DEDUPE()"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    (is (string= "DEDUPE" (sel::node-s opt)))
    (is (string= "DATA" (sel::node-s (first (sel::node-items opt))))))

  ;; 13. Trivial FILTER(TRUE) elimination
  (let* ((prog (sel:compile-source "DATA .> FILTER(TRUE) .> TAKE(10)"))
         (opt (sel:optimize-ast-logical (sel:program-ast prog))))
    (is (string= "TAKE" (sel::node-s opt)))
    (is (string= "DATA" (sel::node-s (first (sel::node-items opt))))))

  ;; 14. In-Memory LINK Predicate Pushdown (Left and Right)
  (let* ((prog (sel:compile-source "ORDERS .> LINK(PRODUCTS, _['p_id'] == _2['id']) .> FILTER(_['orders']['status'] $== 'ACTIVE' AND _['products']['is_active'] == 1)"))
         (opt (sel:optimize-ast-in-memory (sel:program-ast prog))))
    (is (string= "LINK" (sel::node-s opt)))
    ;; Left side of LINK should be rewritten to FILTER on ORDERS
    (let ((left-side (first (sel::node-items opt))))
      (is (string= "FILTER" (sel::node-s left-side)))
      (is (string= "ORDERS" (sel::node-s (first (sel::node-items left-side))))))
    ;; Right side of LINK should be rewritten to FILTER on PRODUCTS
    (let ((right-side (second (sel::node-items opt))))
      (is (string= "FILTER" (sel::node-s right-side)))
      (is (string= "PRODUCTS" (sel::node-s (first (sel::node-items right-side))))))))
