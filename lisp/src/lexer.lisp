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
  '("$==" "$!=" "$<=" "$>="
    "$<" "$>" "==" "!=" "<=" ">=" "+=" "-=" "*=" "/=" "%=" "&="
    "+" "-" "*" "/" "%" "&" "=" "<" ">" "(" ")" "[" "]" "," ";"))

(defparameter +reserved+
  '("TRUE" "FALSE" "AND" "OR" "NOT" "XOR" "EQL" "IN" "BAND" "BOR" "BXOR"))

(defun reservedp (word) (member word +reserved+ :test #'string=))

(defstruct (token (:constructor make-token (type value pos)))
  (type :eof :type keyword)     ; :num :text :ident :op :eof
  (value "" :type string)
  (pos nil))

(defun sel-digit-p (c) (char<= #\0 c #\9))
(defun sel-alpha-p (c)
  (or (char<= #\A c #\Z) (char<= #\a c #\z) (char= c #\_)))
(defun sel-ident-p (c) (or (sel-alpha-p c) (sel-digit-p c)))
(defun sel-space-p (c)
  (or (char= c #\Space) (char= c #\Tab) (char= c #\Return) (char= c #\Newline)))

(defstruct (lexer (:constructor %make-lexer))
  (chars "" :type string)
  (n 0 :type fixnum)
  (line-starts nil :type list))

(defun make-lexer (source)
  ;; A lone surrogate here is E_UTF8 rather than a silently mangled token.
  (unless (valid-utf8-string-p source)
    (fail "E_UTF8" "source carries an unpaired surrogate"))
  (let ((starts (list 0)))
    (loop for i from 0 below (length source)
          when (char= (char source i) #\Newline)
            do (push (1+ i) starts))
    (%make-lexer :chars source :n (length source) :line-starts (nreverse starts))))

(defun lexer-pos-at (lx offset)
  (let ((line 1)
        (start 0))
    (loop for s in (lexer-line-starts lx)
          for k from 1
          while (<= s offset)
          do (setf line k start s))
    (make-pos line (1+ (- offset start)) offset)))

(defun lexer-slice (lx from to) (subseq (lexer-chars lx) from to))

(defun tokenize (source)
  (let ((lx (make-lexer source))
        (out '()))
    (setf out (lex-range lx 0 (lexer-n lx) out))
    (nreverse (cons (make-token :eof "" (lexer-pos-at lx (lexer-n lx))) out))))

;;; OUT is accumulated reversed; every helper returns the new OUT.
(defun lex-range (lx from to out)
  (let ((i from)
        (chars (lexer-chars lx)))
    (loop while (< i to)
          do (let ((c (char chars i)))
               (cond
                 ((sel-space-p c) (incf i))

                 ((char= c #\#)
                  (loop while (and (< i to) (char/= (char chars i) #\Newline)) do (incf i)))

                 ((sel-digit-p c)
                  (let ((j i)
                        (pos (lexer-pos-at lx i)))
                    (loop while (and (< j to) (sel-digit-p (char chars j))) do (incf j))
                    ;; Only consume the dot when a digit follows, so `1.` is not
                    ;; a number.
                    (when (and (< (1+ j) to)
                               (char= (char chars j) #\.)
                               (sel-digit-p (char chars (1+ j))))
                      (incf j)
                      (loop while (and (< j to) (sel-digit-p (char chars j))) do (incf j)))
                    (push (make-token :num (lexer-slice lx i j) pos) out)
                    (setf i j)))

                 ((sel-alpha-p c)
                  (let ((j i)
                        (pos (lexer-pos-at lx i)))
                    (loop while (and (< j to) (sel-ident-p (char chars j))) do (incf j))
                    ;; Identifiers are ASCII and case-insensitive; upper case is
                    ;; canonical. Only A-Z, never the host's Unicode upcasing.
                    (push (make-token :ident (ascii-upcase (lexer-slice lx i j)) pos) out)
                    (setf i j)))

                 ((char= c #\")
                  (multiple-value-setq (i out) (lex-quoted lx i to out)))

                 ((char= c #\')
                  (multiple-value-setq (i out) (lex-raw lx i to out)))

                 (t
                  (let ((op (match-operator lx i to))
                        (pos (lexer-pos-at lx i)))
                    (unless op
                      (fail "E_SYNTAX" (format nil "unexpected character ~s" (string c)) pos))
                    (push (make-token :op op pos) out)
                    (incf i (length op)))))))
    out))

(defun ascii-upcase (s)
  (map 'string (lambda (c) (if (char<= #\a c #\z)
                               (code-char (- (char-code c) 32))
                               c))
       s))

(defun match-operator (lx i to)
  (let ((chars (lexer-chars lx)))
    (loop for op in +operators+
          when (and (<= (+ i (length op)) to)
                    (string= op chars :start2 i :end2 (+ i (length op))))
            do (return op))))

;;; --- text literals ---------------------------------------------------------

;;; Raw 'literals' take no escapes and no interpolation; '' is one quote. This is
;;; the form to use for regex patterns.
(defun lex-raw (lx start to out)
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
                        (push (make-token :text (get-output-stream-string buf) pos) out)
                        (return-from lex-raw (values (1+ i) out)))))
                 (t (write-char c buf) (incf i)))))
    (fail "E_UNTERMINATED" "unterminated raw text literal" pos)))

(defun lex-quoted (lx start to out)
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
                  (return-from lex-quoted
                    (values (1+ i) (emit-parts lx (nreverse parts) pos out))))

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
                       (format nil "code point U+~a is not encodable" (string-upcase h))
                       pos))
               (values (string (code-char cp)) (1+ j))))))
        (t (fail "E_ESCAPE" (format nil "unknown escape \\~a" e) pos))))))

;;; Returns the index just past the matching '}'. Nested literals are skipped so
;;; that a brace inside a string inside an interpolation does not close it.
(defun match-brace (lx i to)
  (let ((pos (lexer-pos-at lx i))
        (chars (lexer-chars lx))
        (depth 0)
        (j i))
    (loop while (< j to)
          do (let ((c (char chars j)))
               (cond
                 ((char= c #\") (setf j (skip-quoted lx j to)))
                 ((char= c #\') (setf j (skip-raw lx j to)))
                 ((char= c #\{) (incf depth) (incf j))
                 ((char= c #\})
                  (decf depth)
                  (incf j)
                  (when (zerop depth) (return-from match-brace j)))
                 ((char= c #\#)
                  (loop while (and (< j to) (char/= (char chars j) #\Newline)) do (incf j)))
                 (t (incf j)))))
    (fail "E_UNTERMINATED" "unterminated { in text literal" pos)))

(defun skip-quoted (lx j to)
  (let ((pos (lexer-pos-at lx j))
        (chars (lexer-chars lx))
        (k (1+ j)))
    (loop while (< k to)
          do (let ((c (char chars k)))
               (cond
                 ((char= c #\\) (incf k 2))
                 ((char= c #\") (return-from skip-quoted (1+ k)))
                 ((char= c #\{) (setf k (match-brace lx k to)))
                 (t (incf k)))))
    (fail "E_UNTERMINATED" "unterminated text literal" pos)))

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

;;; A literal with no interpolation is one token. Otherwise it becomes the tokens
;;; of `( "seg" & expr & "seg" )` — empty segments included, so the result always
;;; goes through `&` and obeys §5.2.
(defun emit-parts (lx parts pos out)
  (if (= (length parts) 1)
      (cons (make-token :text (second (first parts)) pos) out)
      (let ((acc out))
        (push (make-token :op "(" pos) acc)
        (loop for part in parts
              for k from 0
              do (when (plusp k) (push (make-token :op "&" pos) acc))
                 (if (eq (first part) :text)
                     (push (make-token :text (second part) pos) acc)
                     (destructuring-bind (from to) (rest part)
                       (let ((mark (length acc)))
                         (push (make-token :op "(" (lexer-pos-at lx from)) acc)
                         (setf acc (lex-range lx from to acc))
                         (when (= (length acc) (1+ mark))
                           (fail "E_SYNTAX" "empty interpolation {}" (lexer-pos-at lx from)))
                         (push (make-token :op ")" (lexer-pos-at lx to)) acc)))))
        (push (make-token :op ")" pos) acc)
        acc)))
