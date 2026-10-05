;;;; Tokeniser. See spec/grammar.md.
;;;;
;;;; A CL string is already a sequence of code points, so every offset, line and
;;;; column here is a code point index without any conversion — which is what
;;;; keeps reported positions identical across hosts.
;;;;
;;;; String interpolation is resolved here and nowhere else: a literal containing
;;;; {…} is emitted as the token stream of a parenthesised `&` chain, so the
;;;; parser never learns that interpolation exists.

(in-package #:sel)

;;; Longest match first: `$<=` must not lex as `$<` followed by `=`.
(defparameter +operators+
  '("???" "??"
    "$==" "$!=" "$<=" "$>="
    "$<" "$>" "==" "!=" "<=" ">=" "+=" "-=" "*=" "/=" "%=" "&="
    ".>"
    "+" "-" "*" "/" "%" "&" "=" "<" ">" "(" ")" "[" "]" "," ";"))

(defparameter +reserved+
  '("TRUE" "FALSE" "NULL" "AND" "OR" "NOT" "XOR" "EQL" "IN" "BAND" "BOR" "BXOR"))

(defun reservedp (word) (member word +reserved+ :test #'string=))

(defstruct (token (:constructor make-token (type value pos)))
  (type :eof :type keyword)     ; :num :text :ident :op :eof
  (value "" :type string)
  (pos nil))

(defun sel-alpha-p (c)
  (or (char<= #\A c #\Z) (char<= #\a c #\z) (char= c #\_)))
(defun sel-ident-p (c) (or (sel-alpha-p c) (ascii-digit-p c)))

(defstruct (lexer (:constructor %make-lexer))
  (chars "" :type string)
  (n 0 :type fixnum)
  (line-starts (make-array 0 :element-type 'fixnum) :type (simple-array fixnum (*)))
  ;; brace-ends[i] is the index just past the } matching the { at i, once some
  ;; scan has established it (0 = not yet). See MATCH-BRACE.
  (brace-ends (make-array 0 :element-type 'fixnum) :type (simple-array fixnum (*)))
  ;; The token stream, accumulated reversed, and how many tokens it holds.
  (out '() :type list)
  (count 0 :type fixnum))

(defun make-lexer (source)
  (let ((starts (list 0)))
    (loop for i from 0 below (length source)
          when (char= (char source i) #\Newline)
            do (push (1+ i) starts))
    (let ((lx (%make-lexer :chars source
                           :n (length source)
                           :brace-ends (make-array (length source) :element-type 'fixnum
                                                                   :initial-element 0)
                           :line-starts (coerce (nreverse starts) '(simple-array fixnum (*))))))
      ;; A lone surrogate here is E_UTF8 rather than a silently mangled token,
      ;; reported where it stands (spec/SPEC.md section 2).
      (let ((bad (position-if (lambda (ch) (<= #xd800 (char-code ch) #xdfff)) source)))
        (when bad
          (fail "E_UTF8" "source carries an unpaired surrogate" (lexer-pos-at lx bad))))
      lx)))

(defun lexer-pos-at (lx offset)
  (declare (optimize (speed 3) (safety 1)))
  (declare (type lexer lx) (type fixnum offset))
  (let* ((starts (lexer-line-starts lx))
         (len (length starts))
         (low 0)
         (high len))
    (declare (type (simple-array fixnum (*)) starts)
             (type fixnum len low high))
    (loop while (< low high)
          do (let ((mid (the fixnum (ash (+ low high) -1))))
               (if (<= (aref starts mid) offset)
                   (setf low (1+ mid))
                   (setf high mid))))
    (let* ((idx (the fixnum (1- low)))
           (start (aref starts idx)))
      (declare (type fixnum idx start))
      (make-pos (the fixnum (1+ idx))
                (the fixnum (1+ (- offset start)))
                offset))))

(defun lexer-slice (lx from to) (subseq (lexer-chars lx) from to))

(defun emit (lx type value pos)
  (push (make-token type value pos) (lexer-out lx))
  (incf (lexer-count lx)))

(defun tokenize (source)
  (let ((lx (make-lexer source)))
    (lex-range lx 0 (lexer-n lx))
    (emit lx :eof "" (lexer-pos-at lx (lexer-n lx)))
    (nreverse (lexer-out lx))))

;;; Lexes chars[from, to) onto the lexer's token stream. Interpolation nests
;;; without bound, so this is a loop over an explicit stack of tasks rather than
;;; a recursion: a literal pushes what it still has to emit (its parts, each
;;; interior range, the closers) and the loop pops them in source order. Nothing
;;; here can therefore reach the host's own control stack, however deep the
;;; braces go.
(defun lex-range (lx from to)
  (let ((stack (list (list :range from to nil))))
    (loop while stack
          do (let ((task (pop stack)))
               (ecase (first task)
                 (:range (setf stack (lex-tokens lx (second task) (third task) (fourth task) stack)))
                 (:part (setf stack (emit-part lx task stack)))
                 (:close
                  ;; An interpolation that lexed to nothing: `{}`, `{ }`, `{# c\n}`.
                  (destructuring-bind (mark from to bal) (rest task)
                    (when (= (lexer-count lx) (1+ mark))
                      (fail "E_SYNTAX" "empty interpolation {}" (lexer-pos-at lx from)))
                    ;; ... and one whose parentheses do not close inside the braces.
                    (when (car bal)
                      (fail "E_SYNTAX"
                            (format nil "unclosed ~a in interpolation" (car (car bal)))
                            (lexer-pos-at lx to)))
                    (emit lx :op ")" (lexer-pos-at lx to))))
                 (:end (emit lx :op ")" (second task))))))))

;;; The flat part of LEX-RANGE. A quoted literal with parts ends the run: the
;;; tasks it pushes come first and the rest of the range resumes after them.
;;; Returns the task stack.
;;;
;;; BAL is the box (a cons whose car is the stack) of parentheses and brackets
;;; open so far in an interpolation body, NIL at the top level, where the parser
;;; does the balancing. A body is spliced into the surrounding tokens as
;;; `( body )`, so a body that closes what it never opened, or leaves something
;;; open, would change the meaning of the text around it; each body has to
;;; balance inside its own braces. Only the body's direct tokens count.
(defun lex-tokens (lx from to bal stack)
  (let ((i from)
        (chars (lexer-chars lx)))
    (loop while (< i to)
          do (let ((c (char chars i)))
               (cond
                 ((ascii-space-p c) (incf i))

                 ((char= c #\#)
                  (loop while (and (< i to) (char/= (char chars i) #\Newline)) do (incf i)))

                 ((ascii-digit-p c)
                  (let ((j i)
                        (pos (lexer-pos-at lx i)))
                    (loop while (and (< j to) (ascii-digit-p (char chars j))) do (incf j))
                    ;; Only consume the dot when a digit follows, so `1.` is not
                    ;; a number.
                    (when (and (< (1+ j) to)
                               (char= (char chars j) #\.)
                               (ascii-digit-p (char chars (1+ j))))
                      (incf j)
                      (loop while (and (< j to) (ascii-digit-p (char chars j))) do (incf j)))
                    (emit lx :num (lexer-slice lx i j) pos)
                    (setf i j)))

                 ((sel-alpha-p c)
                  (let ((j i)
                        (pos (lexer-pos-at lx i)))
                    (loop while (and (< j to) (sel-ident-p (char chars j))) do (incf j))
                    ;; Identifiers are ASCII and case-insensitive; upper case is
                    ;; canonical. Only A-Z, never the host's Unicode upcasing.
                    (emit lx :ident (ascii-upcase (lexer-slice lx i j)) pos)
                    (setf i j)))

                 ((char= c #\")
                  (let ((pos (lexer-pos-at lx i)))
                    (multiple-value-bind (parts next) (scan-quoted lx i to)
                      (cond
                        ((= (length parts) 1)
                         (emit lx :text (second (first parts)) pos)
                         (setf i next))
                        (t
                         ;; `( "seg" & expr & "seg" )`: the opener now, the rest
                         ;; as tasks, the remainder of this range underneath them.
                         (emit lx :op "(" pos)
                         (push (list :range next to bal) stack)
                         (push (list :end pos) stack)
                         (loop for part in (reverse parts)
                               for k downfrom (1- (length parts))
                               do (push (list :part part k pos) stack))
                         (return-from lex-tokens stack))))))

                 ((char= c #\')
                  (setf i (lex-raw lx i to)))

                 (t
                  (let ((op (match-operator lx i to))
                        (pos (lexer-pos-at lx i)))
                    (unless op
                      (fail "E_SYNTAX" (format nil "unexpected character ~a" (describe-char c)) pos))
                    (when bal
                      (cond
                        ((or (string= op "(") (string= op "["))
                         (push (list op) (car bal)))
                        ((or (string= op ")") (string= op "]"))
                         (let ((open (pop (car bal))))
                           (when (or (null open)
                                     (not (eq (string= (car open) "(") (string= op ")"))))
                             (fail "E_SYNTAX"
                                   (format nil "unbalanced ~a in interpolation" op)
                                   pos))))))
                    (emit lx :op op pos)
                    (incf i (length op)))))))
    stack))

;;; One part of an interpolated literal: the `&` before it, then either its text
;;; or `( interior )`, the interior being a range of its own. Returns the stack.
(defun emit-part (lx task stack)
  (destructuring-bind (part index pos) (rest task)
    (when (plusp index) (emit lx :op "&" pos))
    (if (eq (first part) :text)
        (emit lx :text (second part) pos)
        (destructuring-bind (from to) (rest part)
          (let ((mark (lexer-count lx))
                (bal (list nil)))
            (emit lx :op "(" (lexer-pos-at lx from))
            (push (list :close mark from to bal) stack)
            (push (list :range from to bal) stack))))
    stack))

(defparameter +operators-by-first-char+
  (let ((table (make-array 128 :initial-element nil)))
    ;; The candidates for each first character, in +OPERATORS+ order: longest
    ;; match first is kept because the order within a bucket is the order of the
    ;; list above.
    (dolist (op (reverse +operators+))
      (push op (svref table (char-code (char op 0)))))
    table)
  "+OPERATORS+ indexed by the first character of each operator: testing
33 strings for every operator token cost more than the rest of lexing it.")

(defun match-operator (lx i to)
  (let* ((chars (lexer-chars lx))
         (code (char-code (char chars i))))
    (when (< code 128)
      (loop for op in (svref +operators-by-first-char+ code)
            when (and (<= (+ i (length op)) to)
                      (string= op chars :start2 i :end2 (+ i (length op))))
              do (return op)))))

;;; --- text literals ---------------------------------------------------------

;;; Raw 'literals' take no escapes and no interpolation; '' is one quote. This is
;;; the form to use for regex patterns.
(defun lex-raw (lx start to)
  (let ((pos (lexer-pos-at lx start))
        (chars (lexer-chars lx))
        (i (1+ start))
        (buf (make-string-output-stream)))
    (loop while (< i to)
          do (let ((c (char chars i)))
               (cond
                 ((char= c #\')
                  (if (and (< (1+ i) to) (char= (char chars (1+ i)) #\'))
                      (progn (write-char #\' buf) (incf i 2))
                      (progn
                        (emit lx :text (get-output-stream-string buf) pos)
                        (return-from lex-raw (1+ i)))))
                 (t (write-char c buf) (incf i)))))
    (fail "E_UNTERMINATED" "unterminated raw text literal" pos)))

;;; Reads a quoted literal into its parts and the index just past its closing
;;; quote, emitting nothing. Every `{...}` is skipped by MATCH-BRACE, so the
;;; interior is not read here, only located.
(defun scan-quoted (lx start to)
  (let ((pos (lexer-pos-at lx start))
        (chars (lexer-chars lx))
        (i (1+ start))
        (parts '())
        (buf (make-string-output-stream)))
    (loop while (< i to)
          do (let ((c (char chars i)))
               (cond
                 ((char= c #\")
                  (push (list :text (get-output-stream-string buf)) parts)
                  (return-from scan-quoted (values (nreverse parts) (1+ i))))

                 ((char= c #\\)
                  (multiple-value-bind (text next) (read-escape lx i to)
                    (write-string text buf)
                    (setf i next)))

                 ((char= c #\{)
                  (let ((close (1- (match-brace lx i to))))   ; index of the matching }
                    (push (list :text (get-output-stream-string buf)) parts)
                    (push (list :expr (1+ i) close) parts)
                    (setf i (1+ close))))

                 (t (write-char c buf) (incf i)))))
    (fail "E_UNTERMINATED" "unterminated text literal" pos)))

(defun read-escape (lx i to)
  (let ((pos (lexer-pos-at lx i))
        (chars (lexer-chars lx)))
    (when (>= (1+ i) to)
      (fail "E_UNTERMINATED" "text literal ends in a backslash" pos))
    (let ((e (char chars (1+ i))))
      (case e
        (#\\ (values "\\" (+ i 2)))
        (#\" (values "\"" (+ i 2)))
        (#\n (values (string #\Newline) (+ i 2)))
        (#\t (values (string #\Tab) (+ i 2)))
        (#\r (values (string #\Return) (+ i 2)))
        (#\{ (values "{" (+ i 2)))
        (#\} (values "}" (+ i 2)))
        (#\u
         (unless (and (< (+ i 2) to) (char= (char chars (+ i 2)) #\{))
           (fail "E_ESCAPE" "\\u must be followed by {" pos))
         (let ((j (+ i 3))
               (hex (make-string-output-stream)))
           (loop while (and (< j to) (char/= (char chars j) #\}))
                 do (write-char (char chars j) hex) (incf j))
           (when (>= j to) (fail "E_UNTERMINATED" "unterminated \\u{...} escape" pos))
           (let ((h (get-output-stream-string hex)))
             (unless (and (plusp (length h))
                          (<= (length h) 6)
                          (every (lambda (c) (ascii-hex-value c)) h))
               (fail "E_ESCAPE" (format nil "bad \\u{~a} escape" h) pos))
             (let ((cp (parse-integer h :radix 16)))
               (when (or (> cp #x10ffff) (<= #xd800 cp #xdfff))
                 (fail "E_RANGE"
                       (format nil "code point U+~a is not encodable" (ascii-upcase h))
                       pos))
               (values (string (code-char cp)) (1+ j))))))
        (t (fail "E_ESCAPE" (format nil "unknown escape \\~a" e) pos))))))

;;; What MATCH-BRACE has open: a brace (counting its own nested plain braces) or
;;; a string.
(defstruct (open-frame (:constructor make-open-frame (strp at)))
  strp (at 0 :type fixnum) (depth 0 :type fixnum))

;;; Returns the index just past the matching '}'. Nested literals are skipped so
;;; that a brace inside a string inside an interpolation does not close it.
;;;
;;; One pass with an explicit stack of what is open (a brace, a string), not a
;;; recursion through the strings, and every brace it closes is remembered in
;;; BRACE-ENDS. The second half is what keeps the lexer linear: a literal nested
;;; d deep is located by its parent and again by each of its own ancestors'
;;; interiors being lexed, and without the memo each of those locate-passes
;;; re-read everything below it. If anything is unterminated the innermost open
;;; construct is the one reported, which is where the recursion used to fail.
(defun match-brace (lx i to)
  (let ((memo (aref (lexer-brace-ends lx) i)))
    (when (/= memo 0) (return-from match-brace memo)))
  (let ((chars (lexer-chars lx))
        (open (list (make-open-frame nil i)))
        (j i))
    (loop
      (let ((top (first open)))
        (when (>= j to)
          (fail "E_UNTERMINATED"
                (if (open-frame-strp top)
                    "unterminated text literal"
                    "unterminated { in text literal")
                (lexer-pos-at lx (open-frame-at top))))
        (let ((c (char chars j)))
          (cond
            ((open-frame-strp top)
             (cond
               ((char= c #\\) (incf j 2))
               ((char= c #\") (pop open) (incf j))
               ((char= c #\{) (push (make-open-frame nil j) open))
               (t (incf j))))
            ((char= c #\") (push (make-open-frame t j) open) (incf j))
            ((char= c #\') (setf j (skip-raw lx j to)))
            ((char= c #\{) (incf (open-frame-depth top)) (incf j))
            ((char= c #\})
             (decf (open-frame-depth top))
             (incf j)
             (when (zerop (open-frame-depth top))
               (setf (aref (lexer-brace-ends lx) (open-frame-at top)) j)
               (pop open)
               (when (null open) (return-from match-brace j))))
            ((char= c #\#)
             (loop while (and (< j to) (char/= (char chars j) #\Newline)) do (incf j)))
            (t (incf j))))))))

(defun skip-raw (lx j to)
  (let ((pos (lexer-pos-at lx j))
        (chars (lexer-chars lx))
        (k (1+ j)))
    (loop while (< k to)
          do (if (char= (char chars k) #\')
                 (if (and (< (1+ k) to) (char= (char chars (1+ k)) #\'))
                     (incf k 2)
                     (return-from skip-raw (1+ k)))
                 (incf k)))
    (fail "E_UNTERMINATED" "unterminated raw text literal" pos)))
