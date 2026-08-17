;;;; Recursive descent, one function per precedence level, mirroring
;;;; spec/grammar.md exactly so that the four implementations can be read side by
;;;; side.

(in-package #:sel)

(defconstant +max-depth+ 200)

(defstruct (node (:constructor make-node (kind pos)))
  ;; :num :text :bool :var :index :seq :list :un :bin :assign :call
  (kind :num :type keyword)
  (pos nil)
  (s "" :type string)      ; num/text literal, var name, or operator
  (b nil)                  ; :bool value
  (grouped nil)            ; came from ( ), so F((1,2)) passes one list not two args
  (l nil)                  ; bin/index/assign left; un operand
  (r nil)                  ; bin/index/assign right
  (items nil :type list)   ; seq/list items, call arguments
  (spec nil))              ; call

(defparameter +assign-ops+ '("=" "+=" "-=" "*=" "/=" "%=" "&="))
(defparameter +compare-ops+
  '("==" "!=" "<" "<=" ">" ">=" "$==" "$!=" "$<" "$<=" "$>" "$>="))

(defun assign-op-p (tok)
  (and (eq (token-type tok) :op) (member (token-value tok) +assign-ops+ :test #'string=)))
(defun compare-op-p (tok)
  (and (eq (token-type tok) :op) (member (token-value tok) +compare-ops+ :test #'string=)))
(defun compare-word-p (tok)
  (and (eq (token-type tok) :ident)
       (or (string= (token-value tok) "EQL") (string= (token-value tok) "IN"))))

(defun describe-token (tok)
  (case (token-type tok)
    (:eof "end of input")
    (:text "a text literal")
    (:num (format nil "number ~a" (token-value tok)))
    (t (format nil "~s" (token-value tok)))))

(defun arity-text (spec)
  (let ((plural (if (= (spec-min spec) 1) "" "s")))
    (cond ((>= (spec-max spec) +variadic+)
           (format nil "at least ~d argument~a" (spec-min spec) plural))
          ((= (spec-min spec) (spec-max spec))
           (format nil "~d argument~a" (spec-min spec) plural))
          (t (format nil "~d to ~d arguments" (spec-min spec) (spec-max spec))))))

(defstruct (parser (:constructor %make-parser (toks)))
  (toks #() :type vector)
  (i 0 :type fixnum)
  (depth 0 :type fixnum))

(defun p-peek (p) (aref (parser-toks p) (parser-i p)))
(defun p-next (p) (prog1 (aref (parser-toks p) (parser-i p)) (incf (parser-i p))))
(defun p-at-op (p v)
  (let ((tok (p-peek p)))
    (and (eq (token-type tok) :op) (string= (token-value tok) v))))
(defun p-at-word (p v)
  (let ((tok (p-peek p)))
    (and (eq (token-type tok) :ident) (string= (token-value tok) v))))
(defun p-at-eof (p) (eq (token-type (p-peek p)) :eof))

(defun p-expect-op (p v)
  (unless (p-at-op p v)
    (let ((tok (p-peek p)))
      (fail "E_SYNTAX" (format nil "expected ~s, got ~a" v (describe-token tok))
            (token-pos tok))))
  (p-next p))

(defmacro with-depth ((p pos) &body body)
  `(progn
     (incf (parser-depth ,p))
     (when (> (parser-depth ,p) +max-depth+)
       (fail "E_DEPTH" "expression nested too deeply" ,pos))
     (unwind-protect (progn ,@body)
       (decf (parser-depth ,p)))))

(defun parse-source (source)
  (let ((p (%make-parser (coerce (tokenize source) 'vector))))
    (parse-program p)))

(defun parse-program (p)
  (let ((node (parse-sequence p)))
    (unless (p-at-eof p)
      (let ((tok (p-peek p)))
        (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok)) (token-pos tok))))
    node))

;;; sequence = list { ";" list } [ ";" ]
(defun parse-sequence (p)
  (with-depth (p (token-pos (p-peek p)))
    (let ((items (list (parse-list p))))
      (loop while (p-at-op p ";")
            do (p-next p)
               ;; A trailing ';' before a closer or end of input is permitted.
               (when (or (p-at-eof p) (p-at-op p ")") (p-at-op p "]")) (return))
               (push (parse-list p) items))
      (setf items (nreverse items))
      (if (= (length items) 1)
          (first items)
          (let ((n (make-node :seq (node-pos (first items)))))
            (setf (node-items n) items)
            n)))))

;;; list = assignment { "," assignment }
(defun parse-list (p)
  (let ((items (list (parse-assignment p))))
    (loop while (p-at-op p ",")
          do (p-next p)
             (push (parse-assignment p) items))
    (setf items (nreverse items))
    (if (= (length items) 1)
        (first items)
        (let ((n (make-node :list (node-pos (first items)))))
          (setf (node-items n) items)
          n))))

;;; assignment = disjunction [ assign_op assignment ]   (right associative)
(defun parse-assignment (p)
  (let ((left (parse-or p)))
    (if (assign-op-p (p-peek p))
        (let ((op (p-next p)))
          (check-target left op)
          (let ((value (parse-assignment p))
                (n (make-node :assign (node-pos left))))
            (setf (node-s n) (token-value op)
                  (node-l n) left
                  (node-r n) value)
            n))
        left)))

(defun parse-or (p) (parse-word-binary p "OR" #'parse-xor))
(defun parse-xor (p) (parse-word-binary p "XOR" #'parse-and))
(defun parse-and (p) (parse-word-binary p "AND" #'parse-not))

(defun parse-word-binary (p word sub)
  (let ((left (funcall sub p)))
    (loop while (p-at-word p word)
          do (let* ((op (p-next p))
                    (right (funcall sub p))
                    (n (make-node :bin (token-pos op))))
               (setf (node-s n) word (node-l n) left (node-r n) right)
               (setf left n)))
    left))

;;; negation = "NOT" negation | comparison
(defun parse-not (p)
  (if (p-at-word p "NOT")
      (let* ((op (p-next p))
             (n (make-node :un (token-pos op))))
        (setf (node-s n) "NOT" (node-l n) (parse-not p))
        n)
      (parse-comparison p)))

;;; comparison = bit_or [ compare_op bit_or ]   — deliberately non-associative
(defun parse-comparison (p)
  (let ((left (parse-bit-or p))
        (tok (p-peek p)))
    (if (not (or (compare-op-p tok) (compare-word-p tok)))
        left
        (progn
          (p-next p)
          (let ((right (parse-bit-or p))
                (after (p-peek p)))
            (when (or (compare-op-p after) (compare-word-p after))
              (fail "E_SYNTAX"
                    (format nil "comparison operators do not chain — parenthesise, as in (a ~a b) AND (b ~a c)"
                            (token-value tok) (token-value after))
                    (token-pos after)))
            (let ((n (make-node :bin (token-pos tok))))
              (setf (node-s n) (token-value tok) (node-l n) left (node-r n) right)
              n))))))

(defun parse-bit-or (p) (parse-word-binary p "BOR" #'parse-bit-xor))
(defun parse-bit-xor (p) (parse-word-binary p "BXOR" #'parse-bit-and))
(defun parse-bit-and (p) (parse-word-binary p "BAND" #'parse-concat))

(defun parse-concat (p) (parse-op-binary p '("&") #'parse-additive))
(defun parse-additive (p) (parse-op-binary p '("+" "-") #'parse-multiplicative))
(defun parse-multiplicative (p) (parse-op-binary p '("*" "/" "%") #'parse-unary))

(defun parse-op-binary (p ops sub)
  (let ((left (funcall sub p)))
    (loop
      (let ((tok (p-peek p)))
        (unless (and (eq (token-type tok) :op)
                     (member (token-value tok) ops :test #'string=))
          (return left))
        (p-next p)
        (let ((n (make-node :bin (token-pos tok))))
          (setf (node-s n) (token-value tok)
                (node-l n) left
                (node-r n) (funcall sub p))
          (setf left n))))))

;;; unary = "-" unary | postfix
(defun parse-unary (p)
  (if (p-at-op p "-")
      (let* ((op (p-next p))
             (n (make-node :un (token-pos op))))
        (setf (node-s n) "NEG" (node-l n) (parse-unary p))
        n)
      (parse-postfix p)))

;;; postfix = primary { "[" sequence "]" }
(defun parse-postfix (p)
  (let ((node (parse-primary p)))
    (loop while (p-at-op p "[")
          do (let* ((br (p-next p))
                    (idx (parse-sequence p)))
               (p-expect-op p "]")
               (let ((n (make-node :index (token-pos br))))
                 (setf (node-l n) node (node-r n) idx)
                 (setf node n))))
    node))

(defun parse-primary (p)
  (let ((tok (p-peek p)))
    (with-depth (p (token-pos tok))
      (case (token-type tok)
        (:num
         (p-next p)
         ;; Canonicalised once, here: the literal 007 is the value 7.
         (let ((n (make-node :num (token-pos tok))))
           (setf (node-s n) (dec-format (dec-parse (token-value tok))))
           n))

        (:text
         (p-next p)
         (let ((n (make-node :text (token-pos tok))))
           (setf (node-s n) (token-value tok))
           n))

        (:ident
         (cond
           ((or (string= (token-value tok) "TRUE") (string= (token-value tok) "FALSE"))
            (p-next p)
            (let ((n (make-node :bool (token-pos tok))))
              (setf (node-b n) (string= (token-value tok) "TRUE"))
              n))
           (t
            (let ((after (aref (parser-toks p) (1+ (parser-i p)))))
              (if (and (eq (token-type after) :op) (string= (token-value after) "("))
                  (parse-call p)
                  (progn
                    (when (reservedp (token-value tok))
                      (fail "E_RESERVED"
                            (format nil "~a is a reserved word and cannot be a variable"
                                    (token-value tok))
                            (token-pos tok)))
                    (p-next p)
                    (let ((n (make-node :var (token-pos tok))))
                      (setf (node-s n) (token-value tok))
                      n)))))))

        (:op
         (if (string= (token-value tok) "(")
             (progn
               (p-next p)
               (when (p-at-op p ")") (fail "E_SYNTAX" "empty parentheses" (token-pos tok)))
               (let ((inner (parse-sequence p)))
                 (p-expect-op p ")")
                 ;; Marked so that F((1,2)) passes one list rather than two
                 ;; arguments. A copy, so the mark does not leak to a shared node.
                 (let ((copy (copy-node inner)))
                   (setf (node-grouped copy) t)
                   copy)))
             (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok))
                   (token-pos tok))))

        (t (fail "E_SYNTAX" (format nil "unexpected ~a" (describe-token tok))
                 (token-pos tok)))))))

(defun parse-call (p)
  (let ((name-tok (p-next p)))
    (p-expect-op p "(")
    (let ((args '()))
      (if (p-at-op p ")")
          (p-next p)
          (let ((inner (parse-sequence p)))
            (p-expect-op p ")")
            (setf args (if (and (eq (node-kind inner) :list) (not (node-grouped inner)))
                           (node-items inner)
                           (list inner)))))
      (let ((spec (registry-lookup (token-value name-tok))))
        (unless spec
          (fail "E_UNKNOWN_FUNC" (format nil "unknown function ~a" (token-value name-tok))
                (token-pos name-tok)))
        (let ((count (length args)))
          (when (or (< count (spec-min spec)) (> count (spec-max spec)))
            (fail "E_ARITY"
                  (format nil "~a takes ~a, got ~d" (spec-name spec) (arity-text spec) count)
                  (token-pos name-tok)))
          (when (spec-arity-error spec)
            (let ((problem (funcall (spec-arity-error spec) count)))
              (when problem (fail "E_ARITY" problem (token-pos name-tok)))))
          (let ((n (make-node :call (token-pos name-tok))))
            (setf (node-s n) (spec-name spec)
                  (node-spec n) spec
                  (node-items n) args)
            n))))))

;;; The target must be an identifier followed by zero or more index operations.
(defun check-target (node op-tok)
  (let ((n node))
    (loop while (eq (node-kind n) :index) do (setf n (node-l n)))
    (when (or (not (eq (node-kind n) :var)) (node-grouped node))
      (fail "E_BAD_ASSIGN"
            (format nil "cannot assign with ~a to this expression" (token-value op-tok))
            (node-pos node)))))
