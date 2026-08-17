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
  (raises "E_NO_SCALAR" (sel:as-text (sel:make-none)))
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
