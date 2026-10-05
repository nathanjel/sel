;;;; The portable regex subset. See spec/SPEC.md §7.8.
;;;;
;;;; A pattern is validated against a whitelist and rewritten before it reaches
;;;; the engine, so anything the engines would disagree about fails loudly here
;;;; instead of producing different answers on different hosts. That pass is
;;;; ported verbatim from the other implementations and is the part that must not
;;;; drift.
;;;;
;;;; Three things are specific to cl-ppcre, and each exists because Perl's rules
;;;; differ from ECMAScript's where SEL has already chosen ECMAScript's:
;;;;
;;;;   1. `^` and `$` are lowered to `\A` and `\z`. cl-ppcre follows Perl, where
;;;;      `$` also matches *before* a trailing newline — the very reason the PHP
;;;;      host compiles with PCRE's `D` modifier. Without this, `RMATCH('^a$',
;;;;      "a\n")` would be TRUE here and FALSE everywhere else.
;;;;   2. Dotall is turned on with :single-line-mode, and multi-line-mode is left
;;;;      off, so `.` means "any code point" and the anchors bind to the whole
;;;;      subject.
;;;;   3. The `i` flag folds the subject before matching. cl-ppcre's
;;;;      case-insensitivity does not fold the two non-ASCII code points that
;;;;      simple-case-fold to ASCII letters, which ECMAScript's `iu` and PCRE2's
;;;;      `ui` both do. See +fold-map+.

(in-package #:sel)

;;; spec/SPEC.md §6.4. PCRE2 and SRELL reject a huge repeat count outright while
;;; JS and cl-ppcre merely never match it, so the subset checker settles it.
(defconstant +max-quantifier+ 65535)   ; PCRE2's own hard limit
(defconstant +regex-max-groups+ +limit-max-regex-groups+)
(defconstant +regex-max-depth+ +limit-max-depth+)

;;; \d, \w and \s are rewritten into explicit ASCII classes rather than passed
;;; through, because PHP's `u` modifier turns on PCRE2's UCP and ECMAScript's
;;; does not. Expanding them here makes the guarantee structural instead of
;;; dependent on a library flag no host fully controls.
(defparameter +expand-outside+
  '((#\d . "[0-9]") (#\D . "[^0-9]")
    (#\w . "[0-9A-Za-z_]") (#\W . "[^0-9A-Za-z_]")
    (#\s . "[ \\t\\n\\r\\f\\x0b]") (#\S . "[^ \\t\\n\\r\\f\\x0b]")))

(defparameter +expand-inside+
  '((#\d . "0-9") (#\w . "0-9A-Za-z_") (#\s . " \\t\\n\\r\\f\\x0b")))

;;; \v is excluded: in PCRE it means "any vertical whitespace", in ECMAScript it
;;; means U+000B. Same spelling, different language.
(defun control-escape-p (e) (member e '(#\n #\r #\t #\f)))

;;; Exactly ECMAScript's u-mode identity escapes; PCRE accepts all of these too.
(defun syntax-char-p (e) (find e "^$\\.*+?()[]{}|/"))

(defun bad-regex (message pattern at pos)
  (fail "E_REGEX_SYNTAX"
        (format nil "~a (at offset ~d of /~a/)" message at pattern)
        pos))

(defun reject-escape (e pattern at pos)
  (cond
    ((member e '(#\b #\B))
     (bad-regex (format nil "\\~a is not portable — word boundaries depend on the engine's idea of a word character, which differs. Use an explicit class such as (^|[^0-9A-Za-z_])" e)
                pattern at pos))
    ((char= e #\v)
     (bad-regex "\\v is not portable — PCRE reads it as any vertical whitespace and ECMAScript as U+000B"
                pattern at pos))
    ((ascii-digit-p e) (bad-regex "backreferences are not portable" pattern at pos))
    ((member e '(#\p #\P)) (bad-regex "\\p{...} is not portable" pattern at pos))
    ((member e '(#\A #\z #\Z #\G #\K))
     (bad-regex (format nil "\\~a is not portable — use ^ and $" e) pattern at pos))
    (t (bad-regex (format nil "unsupported escape \\~a" e) pattern at pos))))

;;; A quantifier may be followed by `?` (lazy). `+` would make it possessive,
;;; which PCRE supports and ECMAScript does not.
(defun after-quantifier (p i pattern pos)
  (cond ((and (< i (length p)) (char= (char p i) #\+))
         (bad-regex "possessive quantifiers are not portable" pattern i pos))
        ((and (< i (length p)) (char= (char p i) #\?)) (1+ i))
        (t i)))

(defun validate-braces (p start pattern pos)
  "Validate a {n}, {n,} or {n,m} quantifier and return the index just past it.

The other hosts get the reversed-bound check free from their engines —
JS, PCRE2, SRELL, Python's re, RE2 and the regex crate all reject {2,1} as a
syntax error. cl-ppcre accepts it and
matches nothing, so the check has to be explicit here. That is the general shape
of the risk in this file: every rule the other hosts delegate to their engine has
to be written out, because cl-ppcre is the most permissive of the engines."
  (let ((i (1+ start))
        (n (length p))
        (lo-start (1+ start))
        lo hi)
    (loop while (and (< i n) (ascii-digit-p (char p i))) do (incf i))
    (when (= i lo-start)
      (bad-regex "{ must begin a quantifier such as {2,4} — escape it as \\{" pattern start pos))
    (setf lo (parse-integer (subseq p lo-start i)))
    (when (and (< i n) (char= (char p i) #\,))
      (incf i)
      (let ((hi-start i))
        (loop while (and (< i n) (ascii-digit-p (char p i))) do (incf i))
        (when (> i hi-start)
          (setf hi (parse-integer (subseq p hi-start i))))))
    (unless (and (< i n) (char= (char p i) #\}))
      (bad-regex "malformed quantifier" pattern start pos))
    (when (or (> lo +max-quantifier+) (and hi (> hi +max-quantifier+)))
      (bad-regex (format nil "quantifier bound exceeds the maximum of ~d" +max-quantifier+)
                 pattern start pos))
    (when (and hi (< hi lo))
      (bad-regex (format nil "quantifier {~d,~d} is empty — the upper bound is below the lower one"
                         lo hi)
                 pattern start pos))
    (1+ i)))

;;; Returns (values rewritten-text index-just-past-the-closing-bracket).
(defun validate-class (p start pattern pos)
  (let ((i (1+ start))
        (n (length p))
        (out (make-string-output-stream))
        (count 0)
        (prev-esc nil))       ; the last item was \d \w or \s
    (write-char #\[ out)
    (when (and (< i n) (char= (char p i) #\^))
      (write-char #\^ out)
      (incf i))
    ;; `]` always closes the class. PCRE treats a leading `]` as a literal while
    ;; ECMAScript reads `[]` as an empty class, so neither spelling is portable —
    ;; write `\]` instead.
    (loop while (< i n)
          do (let ((c (char p i)))
               (cond
                 ;; A `[` followed by `:`, `.` or `=` inside a class is refused, closed or
                 ;; not (SPEC 7.8): the POSIX bracket forms [:name:], [.x.], [=x=] are
                 ;; read differently by the engines, and so is an unfinished one.
                 ((and (char= c #\[) (< (1+ i) n) (find (char p (1+ i)) ":.="))
                  (bad-regex "POSIX bracket forms such as [[:alpha:]] are not portable" pattern i pos))
                 ((char= c #\])
                  (when (zerop count)
                    (bad-regex "empty character class — write \\] for a literal bracket"
                               pattern start pos))
                  (write-char #\] out)
                  (return-from validate-class
                    (values (get-output-stream-string out) (1+ i))))
                 (t
                  (incf count)
                  ;; A class escape cannot be the end of a range: `[+-\d]`, `[\d-z]`.
                  ;; PCRE takes the hyphen literally and ECMAScript refuses; a hyphen
                  ;; first or last is a literal and stays legal (SPEC 7.8).
                  (when (and (char= c #\-) (> count 1) (< (1+ i) n) (char/= (char p (1+ i)) #\]))
                    (when prev-esc
                      (bad-regex "a class escape cannot be a range endpoint" pattern i pos))
                    (when (and (char= (char p (1+ i)) #\\) (< (+ i 2) n)
                               (find (char p (+ i 2)) "dDwWsS"))
                      (bad-regex "a class escape cannot be a range endpoint" pattern i pos)))
                  (setf prev-esc nil)
                  (if (char= c #\\)
                      (progn
                        (when (>= (1+ i) n)
                          (bad-regex "trailing backslash in character class" pattern i pos))
                        (let* ((e (char p (1+ i)))
                               (expansion (cdr (assoc e +expand-inside+))))
                          (cond
                            (expansion (write-string expansion out) (setf prev-esc t) (incf i 2))
                            ((member e '(#\D #\W #\S))
                             (bad-regex (format nil "\\~a inside a character class cannot be expressed portably — negate the whole class instead" e)
                                        pattern i pos))
                            ((or (control-escape-p e) (syntax-char-p e) (char= e #\-))
                             (write-char c out)
                             (write-char e out)
                             (incf i 2))
                            (t (reject-escape e pattern i pos)))))
                      (progn (write-char c out) (incf i)))))))
    (bad-regex "unterminated character class" pattern start pos)))

;;; Validates and rewrites in one pass, returning source that means the same
;;; thing to every engine. Every host runs this, so every engine compiles the same
;;; pattern — apart from the anchor lowering noted at the top of this file.
(defun validate-pattern (pattern pos &optional (anchored-at-start t)
                                       (lower-anchors t))
  "Validates and rewrites in one pass, returning source that means the same
thing to every engine.

LOWER-ANCHORS is what the SEL->SQL layer turns off. cl-ppcre's `$` also matches
before a trailing newline, exactly like PCRE and unlike ECMAScript, so this host
lowers `^` and `$` to \\A and \\z for its own engine. The Python host does that
in a separate pass for the stated reason that \"validate()'s output is shared
with the hosts whose engines have no \\A\" -- and a translated pattern goes to a
SERVER, which is one of those. Left on, `RMATCH(\"^a$\", s)` emitted
`(?s)\\Aa\\z` into SQL where every other host emits `(?s)^a$`."
  (let ((p pattern)
        (out (make-string-output-stream))
        (i 0))
    (let ((n (length p)))
      (loop while (< i n)
            do (let ((c (char p i)))
                 (cond
                   ((char= c #\\)
                    (when (>= (1+ i) n) (bad-regex "trailing backslash" pattern i pos))
                    (let* ((e (char p (1+ i)))
                           (expansion (cdr (assoc e +expand-outside+))))
                      (cond
                        (expansion (write-string expansion out) (incf i 2))
                        ((or (control-escape-p e) (syntax-char-p e))
                         (write-char c out) (write-char e out) (incf i 2))
                        (t (reject-escape e pattern i pos)))))

                   ((char= c #\[)
                    (multiple-value-bind (text next) (validate-class p i pattern pos)
                      (write-string text out)
                      (setf i next)))

                   ((char= c #\()
                    (if (and (< (1+ i) n) (char= (char p (1+ i)) #\?))
                        (if (and (< (+ i 2) n) (char= (char p (+ i 2)) #\:))
                            (progn (write-string "(?:" out) (incf i 3))
                            (let* ((k (if (< (+ i 2) n) (char p (+ i 2)) #\Nul))
                                   (kind (cond ((member k '(#\= #\!)) "lookahead")
                                               ((char= k #\<) "lookbehind and named groups")
                                               ((char= k #\>) "atomic groups")
                                               (t "this group type"))))
                              (bad-regex (format nil "~a is not portable — only (?: ) is" kind)
                                         pattern i pos)))
                        (progn (write-char #\( out) (incf i))))

                   ((char= c #\{)
                    (let ((end (after-quantifier p (validate-braces p i pattern pos) pattern pos)))
                      (write-string (subseq p i end) out)
                      (setf i end)))

                   ((member c '(#\* #\+ #\?))
                    (let ((end (after-quantifier p (1+ i) pattern pos)))
                      (write-string (subseq p i end) out)
                      (setf i end)))

                   ((char= c #\}) (bad-regex "unmatched } — escape it as \\}" pattern i pos))
                   ((char= c #\]) (bad-regex "unmatched ] — escape it as \\]" pattern i pos))

                   ;; The cl-ppcre-specific lowering. See the header: Perl's `$`
                   ;; also matches before a trailing newline, and SEL's does not.
                   ;;
                   ;; `^` has a second problem. cl-ppcre binds \A to the :start
                   ;; argument rather than to the true beginning of the string,
                   ;; so a continued scan — which is how RREPLACE walks to the
                   ;; next match — would let `^` match again at each offset.
                   ;; RREPLACE therefore compiles a second scanner in which `^`
                   ;; can never match, and uses it for every match after the
                   ;; first. `[^\s\S]` is the empty class: it matches no
                   ;; character, so any branch requiring it dies, which is what
                   ;; "not at the start of the subject" means here.
                   ((char= c #\^)
                    (write-string (if lower-anchors
                                      (if anchored-at-start "\\A" "[^\\s\\S]")
                                      "^")
                                  out)
                    (incf i))
                   ((char= c #\$) (write-string (if lower-anchors "\\z" "$") out) (incf i))

                   ;; `.` is lowered to the class of every code point for cl-ppcre.
                   ;; Its own dotall `.*` is optimised on the assumption that it can
                   ;; consume to the end, and after an end anchor that assumption
                   ;; loses the empty match: `\z.*` finds nothing in "abc" where
                   ;; `\z[\s\S]*` finds the empty match at 3.
                   ((and (char= c #\.) lower-anchors) (write-string "[\\s\\S]" out) (incf i))

                   (t (write-char c out) (incf i))))))
    (get-output-stream-string out)))

;;; The two non-ASCII code points whose *simple* case folding is an ASCII letter.
;;; ECMAScript's `u` mode and PCRE2's `ui` both apply simple folding, so all the
;;; other hosts match them; cl-ppcre does not. Folding the subject is sound
;;; because each replacement is one character wide, so every offset still refers
;;; to the same position in the original — which is what group text, positions
;;; and replacements are sliced from.
(defparameter +fold-map+
  (list (cons (code-char #x212a) #\k)    ; KELVIN SIGN
        (cons (code-char #x017f) #\s)))  ; LATIN SMALL LETTER LONG S

(defun fold-subject (s)
  "Map the non-ASCII code points that simple-case-fold to an ASCII letter. One
character in, one character out, so offsets are preserved exactly."
  (if (every (lambda (c) (not (assoc c +fold-map+))) s)
      s
      (map 'string (lambda (c) (or (cdr (assoc c +fold-map+)) c)) s)))


;;; --- the pattern's shape (SPEC 7.8) -------------------------------------------
;;;
;;; VALIDATE-PATTERN checks the syntax one character at a time and knows nothing of
;;; nesting. The rules below are about the SHAPE of the pattern -- how deep its
;;; groups go, whether a loop body can match nothing, which captures a loop's
;;; iterations must all set -- so they need the tree, and the tree is built by a
;;; recursive descent over the pattern that the depth cap itself keeps shallow:
;;; a pattern nested past 200 is refused before the parser goes deeper.
;;;
;;; Nodes: (:empty) (:atom) (:anchor) (:cat . items) (:alt . branches)
;;;        (:group index-or-nil node) (:rep node lo hi)   ; hi NIL = unbounded

(defstruct (rxp (:constructor make-rxp (text pos)))
  (text "" :type string)
  (pos nil)
  (i 0 :type fixnum)
  (groups 0 :type fixnum)     ; groups seen, capturing and not
  (captures 0 :type fixnum))  ; capture indexes handed out

(defun rx-nullable-p (node)
  (ecase (car node)
    ((:empty :anchor) t)
    (:atom nil)
    (:cat (every #'rx-nullable-p (cdr node)))
    (:alt (some #'rx-nullable-p (cdr node)))
    (:group (rx-nullable-p (third node)))
    (:rep (or (zerop (third node)) (rx-nullable-p (second node))))))

(defun rx-captures (node)
  "Every capture index inside NODE."
  (ecase (car node)
    ((:empty :anchor :atom) '())
    ((:cat :alt) (loop for x in (cdr node) append (rx-captures x)))
    (:group (append (when (second node) (list (second node))) (rx-captures (third node))))
    (:rep (rx-captures (second node)))))

(defun rx-always-captures (node)
  "The capture indexes every match of NODE sets."
  (ecase (car node)
    ((:empty :anchor :atom) '())
    (:cat (loop for x in (cdr node) append (rx-always-captures x)))
    (:alt (let ((sets (mapcar #'rx-always-captures (cdr node))))
            (if (null sets)
                '()
                (reduce (lambda (a b) (intersection a b)) sets))))
    (:group (append (when (second node) (list (second node)))
                    (rx-always-captures (third node))))
    (:rep (if (zerop (third node)) '() (rx-always-captures (second node))))))

(defun rx-check-loop (body atom-kind pattern at pos)
  "A quantified anchor, a loop whose body can match nothing, a loop holding a
capture some iteration need not set."
  (when (eq atom-kind :anchor)
    (bad-regex "an anchor cannot be quantified" pattern at pos))
  (when (rx-nullable-p body)
    (bad-regex "a loop whose body can match the empty string is not portable" pattern at pos))
  (let ((caps (rx-captures body)))
    (when (and caps (set-difference caps (rx-always-captures body)))
      (bad-regex "a capture inside a loop must take part in every iteration" pattern at pos))))

(defun rx-parse-alt (r depth)
  (let ((branches (list (rx-parse-cat r depth)))
        (p (rxp-text r)))
    (loop while (and (< (rxp-i r) (length p)) (char= (char p (rxp-i r)) #\|))
          do (incf (rxp-i r))
             (push (rx-parse-cat r depth) branches))
    (if (cdr branches)
        (cons :alt (nreverse branches))
        (car branches))))

(defun rx-parse-cat (r depth)
  (let ((items '())
        (p (rxp-text r))
        (pos (rxp-pos r)))
    (loop
      (let ((n (length p)))
        (when (or (>= (rxp-i r) n) (find (char p (rxp-i r)) "|)"))
          (return))
        (let* ((start (rxp-i r))
               (c (char p start))
               (atom nil) (kind :atom))
          (cond
            ((char= c #\()
             (incf (rxp-groups r))
             (when (> (rxp-groups r) +regex-max-groups+)
               (bad-regex "too many groups" p start pos))
             (when (>= depth +regex-max-depth+)
               (bad-regex "groups nested too deeply" p start pos))
             (let ((cap nil))
               (if (and (< (+ start 2) n) (char= (char p (1+ start)) #\?))
                   (setf (rxp-i r) (+ start 3))          ; (?:
                   (progn (setf (rxp-i r) (1+ start))
                          (setf cap (incf (rxp-captures r)))))
               (let ((inner (rx-parse-alt r (1+ depth))))
                 (unless (and (< (rxp-i r) n) (char= (char p (rxp-i r)) #\)))
                   (bad-regex "missing )" p start pos))
                 (incf (rxp-i r))
                 (setf atom (list :group cap inner) kind :group))))
            ((char= c #\[)
             ;; VALIDATE-CLASS has already accepted it; find its close.
             (let ((j (1+ start)))
               (when (and (< j n) (char= (char p j) #\^)) (incf j))
               (loop while (and (< j n) (char/= (char p j) #\]))
                     do (incf j (if (and (char= (char p j) #\\) (< (1+ j) n)) 2 1)))
               (setf (rxp-i r) (min n (1+ j)) atom (list :atom))))
            ((char= c #\\) (setf (rxp-i r) (+ start 2) atom (list :atom)))
            ((or (char= c #\^) (char= c #\$))
             (incf (rxp-i r)) (setf atom (list :anchor) kind :anchor))
            ;; A quantifier with nothing before it. The runtime engine refuses this
            ;; too, but a literal pattern is checked when the program compiles
            ;; (SPEC 7.8), before any engine is asked.
            ((find c "*+?{")
             (bad-regex "nothing to repeat" p start pos))
            (t (incf (rxp-i r)) (setf atom (list :atom))))
          ;; A quantifier binds to the atom just read.
          (loop
            (let ((q (and (< (rxp-i r) n) (char p (rxp-i r))))
                  (lo nil) (hi nil))
              (case q
                (#\* (setf lo 0 hi nil) (incf (rxp-i r)))
                (#\+ (setf lo 1 hi nil) (incf (rxp-i r)))
                (#\? (setf lo 0 hi 1) (incf (rxp-i r)))
                (#\{ (let* ((close (position #\} p :start (rxp-i r)))
                            (body (subseq p (1+ (rxp-i r)) close))
                            (comma (position #\, body)))
                       (setf lo (parse-integer body :end comma))
                       (setf hi (cond ((null comma) lo)
                                      ((= comma (1- (length body))) nil)
                                      (t (parse-integer body :start (1+ comma)))))
                       (setf (rxp-i r) (1+ close))))
                (t (return)))
              ;; a lazy `?`
              (when (and (< (rxp-i r) n) (char= (char p (rxp-i r)) #\?)) (incf (rxp-i r)))
              (when (or (null hi) (> hi 1)) (rx-check-loop atom kind p start pos))
              (when (and (eq kind :anchor)) (rx-check-loop atom kind p start pos))
              (setf atom (list :rep atom lo hi) kind :rep)))
          (push atom items))))
    (cond ((null items) (list :empty))
          ((null (cdr items)) (car items))
          (t (cons :cat (nreverse items))))))

(defun check-regex-shape (pattern pos)
  "The shape rules of SPEC 7.8 for a pattern VALIDATE-PATTERN has accepted."
  (let ((r (make-rxp pattern pos)))
    (rx-parse-alt r 0)
    (when (< (rxp-i r) (length pattern))
      (bad-regex "unmatched )" pattern (rxp-i r) pos))))

;;; --- exponential ambiguity (SPEC 7.8, "Refused for its running time") --------
;;;
;;; A backtracking engine takes exponential time on a pattern that can match some
;;; word in exponentially many ways, so the subset refuses such patterns by one
;;; static rule that every host implements identically. This is a port of
;;; tools/regex-ambiguity-ref.py (the reference; conformance/28b pins it): the
;;; pattern becomes a tree of character SETS (sorted, merged ranges), then a
;;; Glushkov position automaton, then four refusals and a size guard.
;;;
;;; VALIDATE-PATTERN and CHECK-REGEX-SHAPE have already accepted the pattern when
;;; this runs, so the parser below assumes a well-formed one; what it must get right
;;; is the SETS -- `i` folding included -- and the counted-repeat handling.

(defconstant +ax-unroll+ 8)
(defconstant +ax-p-max+ (ash 1 17))      ; positions
(defconstant +ax-e-max+ (ash 1 18))      ; follow edges
(defconstant +ax-d-max+ (ash 1 21))      ; sum over edges of the ranges at the target
(defconstant +ax-q-max+ (ash 1 20))      ; pair-graph work
(defconstant +ax-amb-max+ 16)
(defconstant +ax-sat+ (ash 1 40))
(defconstant +ax-max-cp+ #x10ffff)

(defvar *ax-pattern* "")
(defvar *ax-pos* nil)

(defun ax-reject (why)
  (bad-regex (format nil "~a (the pattern can take exponential time)" why) *ax-pattern* 0 *ax-pos*))

;;; --- sets of code points: lists of (lo . hi), sorted and merged -----------------
(defun ax-range< (x y)
  (or (< (car x) (car y)) (and (= (car x) (car y)) (< (cdr x) (cdr y)))))

(defun ax-norm (rs)
  (let ((out '()))
    (dolist (r (sort (copy-list rs) #'ax-range<))
      (if (and out (<= (car r) (1+ (cdr (car out)))))
          (when (> (cdr r) (cdr (car out)))
            (setf (car out) (cons (car (car out)) (cdr r))))
          (push (cons (car r) (cdr r)) out)))
    (nreverse out)))

(defun ax-negate (rs)
  (let ((out '()) (next 0))
    (dolist (r (ax-norm rs))
      (when (> (car r) next) (push (cons next (1- (car r))) out))
      (setf next (1+ (cdr r))))
    (when (<= next +ax-max-cp+) (push (cons next +ax-max-cp+) out))
    (nreverse out)))

(defun ax-has (rs c) (some (lambda (r) (<= (car r) c (cdr r))) rs))

(defun ax-fold (rs)
  "`i`: simple case folding as far as an ASCII pattern can reach it -- the ASCII
case mirror, U+212A with k/K and U+017F with s/S."
  (let ((extra '()))
    (dolist (r rs)
      (let ((lo (max (car r) #x41)) (hi (min (cdr r) #x5a)))
        (when (<= lo hi) (push (cons (+ lo 32) (+ hi 32)) extra)))
      (let ((lo (max (car r) #x61)) (hi (min (cdr r) #x7a)))
        (when (<= lo hi) (push (cons (- lo 32) (- hi 32)) extra))))
    (let ((all (ax-norm (append rs extra))) (more '()))
      (when (or (ax-has all 107) (ax-has all 75)) (push (cons #x212a #x212a) more))
      (when (or (ax-has all 115) (ax-has all 83)) (push (cons #x17f #x17f) more))
      (ax-norm (append all more)))))

(defun ax-intersects-p (x y)
  (loop while (and x y)
        do (cond ((< (cdr (car x)) (car (car y))) (pop x))
                 ((< (cdr (car y)) (car (car x))) (pop y))
                 (t (return-from ax-intersects-p t))))
  nil)

(defun ax-max-cover (classes)
  "The largest number of the classes that share one code point."
  (let ((ev '()) (best 0) (cur 0))
    (dolist (c classes)
      (dolist (r c)
        (push (cons (car r) 1) ev)
        (push (cons (1+ (cdr r)) -1) ev)))
    ;; closings before openings at a shared point
    (dolist (e (sort ev #'ax-range<))
      (incf cur (cdr e))
      (setf best (max best cur)))
    best))

(defparameter +ax-digit+ (list (cons #x30 #x39)))
(defparameter +ax-word+ (ax-norm (list (cons #x30 #x39) (cons #x41 #x5a) (cons #x5f #x5f) (cons #x61 #x7a))))
(defparameter +ax-space+ (ax-norm (list (cons 9 13) (cons 32 32))))
(defparameter +ax-any+ (list (cons 0 +ax-max-cp+)))

(defun ax-escape-set (e)
  (case e (#\d +ax-digit+) (#\w +ax-word+) (#\s +ax-space+)))

;;; --- the tree --------------------------------------------------------------------
(defstruct (ax (:constructor %ax))
  kind a lo hi nullable (minlen 0) (maxlen 0))

(defun ax-eps () (%ax :kind :eps :nullable t))
(defun ax-let (rs) (%ax :kind :let :a rs :nullable nil :minlen 1 :maxlen 1))
(defun ax-cat (items)
  (%ax :kind :cat :a items
       :nullable (every #'ax-nullable items)
       :minlen (min +ax-sat+ (loop for x in items sum (ax-minlen x)))
       :maxlen (min +ax-sat+ (loop for x in items sum (ax-maxlen x)))))
(defun ax-alt (items)
  (%ax :kind :alt :a items
       :nullable (and (some #'ax-nullable items) t)
       :minlen (reduce #'min items :key #'ax-minlen)
       :maxlen (reduce #'max items :key #'ax-maxlen)))
(defun ax-opt (x)
  (%ax :kind :opt :a x :nullable t :minlen 0 :maxlen (ax-maxlen x)))
(defun ax-rep (x lo hi)
  (%ax :kind :rep :a x :lo lo :hi hi
       :nullable (or (= lo 0) (ax-nullable x))
       :minlen (min +ax-sat+ (* lo (ax-minlen x)))
       :maxlen (cond ((or (eql hi 0) (zerop (ax-maxlen x))) 0)
                     ((null hi) +ax-sat+)
                     (t (min +ax-sat+ (* hi (ax-maxlen x)))))))

;;; --- the parser (well-formed input) ------------------------------------------------
(defun ax-parse (p ic)
  (let ((i 0) (n (length p)))
    (labels ((peek () (if (< i n) (char p i) nil))
             (letter (rs) (let ((rs (ax-norm rs))) (ax-let (if ic (ax-fold rs) rs))))
             (parse-alt ()
               (let ((branches (list (parse-cat))))
                 (loop while (eql (peek) #\|) do (incf i) (push (parse-cat) branches))
                 (if (cdr branches) (ax-alt (nreverse branches)) (car branches))))
             (parse-cat ()
               (let ((items '()))
                 (loop while (and (< i n) (not (find (char p i) "|)")))
                       do (push (parse-quantified) items))
                 (cond ((null items) (ax-eps))
                       ((null (cdr items)) (car items))
                       (t (ax-cat (nreverse items))))))
             (parse-quantified ()
               (let ((atom (parse-atom)))
                 (loop
                   (let ((c (peek)) lo hi)
                     (case c
                       (#\* (setf lo 0 hi nil) (incf i))
                       (#\+ (setf lo 1 hi nil) (incf i))
                       (#\? (setf lo 0 hi 1) (incf i))
                       (#\{ (let* ((close (position #\} p :start i))
                                   (body (subseq p (1+ i) close))
                                   (comma (position #\, body)))
                              (setf lo (parse-integer body :end comma)
                                    hi (cond ((null comma) lo)
                                             ((= comma (1- (length body))) nil)
                                             (t (parse-integer body :start (1+ comma)))))
                              (setf i (1+ close))))
                       (t (return atom)))
                     (when (eql (peek) #\?) (incf i))       ; lazy: same language
                     (setf atom (ax-rep atom lo hi))))))
             (parse-atom ()
               (let ((c (char p i)))
                 (cond
                   ((or (char= c #\^) (char= c #\$)) (incf i) (ax-eps))
                   ((char= c #\.) (incf i) (letter +ax-any+))
                   ((char= c #\()
                    (incf i)
                    (when (eql (peek) #\?) (incf i 2))        ; (?:
                    (let ((inner (parse-alt)))
                      (incf i)                                  ; the )
                      inner))
                   ((char= c #\[) (parse-class))
                   ((char= c #\\) (parse-escape))
                   (t (incf i) (letter (list (cons (char-code c) (char-code c))))))))
             (parse-escape ()
               (let ((e (char p (1+ i))))
                 (incf i 2)
                 (cond ((ax-escape-set e) (letter (ax-escape-set e)))
                       ((member e '(#\D #\W #\S))
                        (letter (ax-negate (ax-escape-set (char-downcase e)))))
                       ((char= e #\n) (letter (list (cons 10 10))))
                       ((char= e #\r) (letter (list (cons 13 13))))
                       ((char= e #\t) (letter (list (cons 9 9))))
                       ((char= e #\f) (letter (list (cons 12 12))))
                       (t (letter (list (cons (char-code e) (char-code e))))))))
             (class-atom (j)
               ;; (values code-point next nil) or (values nil next set) for \d \w \s
               (let ((c (char p j)))
                 (if (char= c #\\)
                     (let ((e (char p (1+ j))))
                       (cond ((ax-escape-set e) (values nil (+ j 2) (ax-escape-set e)))
                             ((char= e #\n) (values 10 (+ j 2) nil))
                             ((char= e #\r) (values 13 (+ j 2) nil))
                             ((char= e #\t) (values 9 (+ j 2) nil))
                             ((char= e #\f) (values 12 (+ j 2) nil))
                             (t (values (char-code e) (+ j 2) nil))))
                     (values (char-code c) (1+ j) nil))))
             (parse-class ()
               (let ((j (1+ i)) (neg nil) (rs '()))
                 (when (and (< j n) (char= (char p j) #\^)) (setf neg t) (incf j))
                 (loop
                   (when (>= j n) (ax-reject "unterminated character class"))
                   (when (char= (char p j) #\]) (incf j) (return))
                   (multiple-value-bind (lo next set) (class-atom j)
                     (setf j next)
                     (cond
                       ((null lo) (setf rs (append set rs)))
                       ((and (< j n) (char= (char p j) #\-) (< (1+ j) n) (char/= (char p (1+ j)) #\]))
                        (multiple-value-bind (hi next2) (class-atom (1+ j))
                          (when (or (null hi) (< hi lo)) (ax-reject "bad range in a class"))
                          (push (cons lo hi) rs)
                          (setf j next2)))
                       (t (push (cons lo lo) rs)))))
                 (setf i j)
                 (let ((set (ax-norm rs)))
                   (when ic (setf set (ax-fold set)))
                   (when neg (setf set (ax-negate set)))
                   (ax-let set)))))
      (parse-alt))))

;;; --- the position automaton ----------------------------------------------------------
(defstruct (axs (:constructor %axs))
  (cls (make-array 16 :adjustable t :fill-pointer 0))
  (succ (make-array 16 :adjustable t :fill-pointer 0))
  (tag (make-hash-table))
  (e 0) (d 0) (amb 0))

(defun make-axs ()
  (let ((s (%axs)))
    (vector-push-extend nil (axs-cls s))
    (vector-push-extend nil (axs-succ s))
    s))

(defun axs-join (s l f sync)
  (incf (axs-e s) (* (length l) (length f)))
  (incf (axs-d s) (* (length l) (loop for q in f sum (length (aref (axs-cls s) q)))))
  (when (or (> (axs-e s) +ax-e-max+) (> (axs-d s) +ax-d-max+))
    (ax-reject "the analysis would be too large"))
  (dolist (a l)
    (dolist (b f)
      (let* ((key (+ (ash a 18) b))
             (old (gethash key (axs-tag s) :none)))
        (cond ((eq old :none)
               (setf (gethash key (axs-tag s)) sync)
               (push b (aref (axs-succ s) a)))
              ((and old sync))            ; two fixed-length loops: allowed
              (t (ax-reject "a follow edge is generated twice")))))))

(defun axs-eps (s k inloop)
  (when (> k 0)
    (when inloop (ax-reject "a nullable choice inside a loop"))
    (incf (axs-amb s) k)
    (when (> (axs-amb s) +ax-amb-max+) (ax-reject "the ambiguity budget is exceeded"))))

(defun axs-walk (s node inloop)
  "-> (values nullable first last); first and last are lists of positions."
  (ecase (ax-kind node)
    (:eps (values t nil nil))
    (:let
     (when (>= (length (axs-cls s)) +ax-p-max+) (ax-reject "too many positions"))
     (vector-push-extend (ax-a node) (axs-cls s))
     (vector-push-extend nil (axs-succ s))
     (let ((i (1- (length (axs-cls s)))))
       (values nil (list i) (list i))))
    (:opt (multiple-value-bind (nl f l) (axs-walk s (ax-a node) inloop)
            (declare (ignore nl))
            (values t f l)))
    (:alt
     (let ((k (count-if #'ax-nullable (ax-a node))) (f '()) (l '()))
       (when (>= k 2) (axs-eps s (integer-length (1- k)) inloop))
       (dolist (b (ax-a node))
         (multiple-value-bind (n2 f2 l2) (axs-walk s b inloop)
           (declare (ignore n2))
           (setf f (append f2 f) l (append l2 l))))
       (values (> k 0) f l)))
    (:cat
     (let ((nl t) (f '()) (l '()))
       (dolist (it (ax-a node))
         (multiple-value-bind (n2 f2 l2) (axs-walk s it inloop)
           (axs-join s l f2 nil)
           (when nl (setf f (append f2 f)))
           (setf l (if n2 (append l2 l) l2))
           (setf nl (and nl n2))))
       (values nl f l)))
    (:rep
     (let ((x (ax-a node)) (lo (ax-lo node)) (hi (ax-hi node)))
       (cond
         ((eql hi 0) (values t nil nil))
         ((and hi (<= hi +ax-unroll+))
          (when (and (= lo 0) (= hi 1) (ax-nullable x)) (axs-eps s 1 inloop))
          (let ((tail (ax-eps)))
            (dotimes (k (- hi lo)) (setf tail (ax-opt (ax-cat (list x tail)))))
            (axs-walk s (ax-cat (append (make-list lo :initial-element x) (list tail))) inloop)))
         (t
          (multiple-value-bind (n2 f l) (axs-walk s x t)
            (declare (ignore n2))
            (axs-join s l f (and (= (ax-minlen x) (ax-maxlen x))
                                 (< 0 (ax-maxlen x) +ax-sat+)))
            (values (or (= lo 0) (ax-nullable x)) f l))))))))

(defun ax-scc (succ)
  "Iterative Tarjan: (values component-id-per-node cyclic-flag-per-node)."
  (let* ((n (length succ))
         (index (make-array n :initial-element -1))
         (low (make-array n :initial-element 0))
         (on (make-array n :initial-element nil))
         (comp (make-array n :initial-element -1))
         (rest (make-array n :initial-element nil))
         (stack '()) (counter 0) (ncomp 0))
    (dotimes (root n)
      (when (= (aref index root) -1)
        (let ((work (list root)))
          (setf (aref index root) counter (aref low root) counter)
          (incf counter)
          (push root stack)
          (setf (aref on root) t (aref rest root) (aref succ root))
          (loop while work
                do (let ((v (car work)))
                     (if (aref rest v)
                         (let ((w (pop (aref rest v))))
                           (cond ((= (aref index w) -1)
                                  (setf (aref index w) counter (aref low w) counter)
                                  (incf counter)
                                  (push w stack)
                                  (setf (aref on w) t (aref rest w) (aref succ w))
                                  (push w work))
                                 ((aref on w)
                                  (setf (aref low v) (min (aref low v) (aref index w))))))
                         (progn
                           (pop work)
                           (when work
                             (let ((u (car work)))
                               (setf (aref low u) (min (aref low u) (aref low v)))))
                           (when (= (aref low v) (aref index v))
                             (loop (let ((w (pop stack)))
                                     (setf (aref on w) nil (aref comp w) ncomp)
                                     (when (= w v) (return))))
                             (incf ncomp)))))))))
    (let ((size (make-array ncomp :initial-element 0))
          (cyc (make-array n :initial-element nil)))
      (dotimes (v n) (incf (aref size (aref comp v))))
      (dotimes (v n)
        (setf (aref cyc v)
              (and (or (> (aref size (aref comp v)) 1) (member v (aref succ v))) t)))
      (values comp cyc))))

(defun ax-analyse (tree)
  (let ((s (make-axs)))
    (multiple-value-bind (nl f l) (axs-walk s tree nil)
      (declare (ignore nl l))
      (setf (aref (axs-succ s) 0) (copy-list f)))
    (let* ((succ (axs-succ s)) (cls (axs-cls s)) (n (length succ)))
      (multiple-value-bind (comp cyc) (ax-scc succ)
        ;; the budget: choices outside every cycle, the start included
        (dotimes (p n)
          (when (and (not (aref cyc p)) (>= (length (aref succ p)) 2))
            (let ((m (ax-max-cover (mapcar (lambda (q) (aref cls q)) (aref succ p)))))
              (when (>= m 2)
                (incf (axs-amb s) (integer-length (1- m)))
                (when (> (axs-amb s) +ax-amb-max+)
                  (ax-reject "the ambiguity budget is exceeded"))))))
        ;; exponential ambiguity: two different paths from a state back to a state
        ;; on one word, found on the pair graph inside each cycle
        (let ((reach (make-hash-table)) (order '()) (fwd (make-hash-table))
              (insc (make-array n :initial-element -1)) (qwork 0))
          (flet ((d (p)
                   (when (< (aref insc p) 0)
                     (setf (aref insc p)
                           (count-if (lambda (q) (= (aref comp q) (aref comp p))) (aref succ p))))
                   (aref insc p)))
            (dotimes (q n)
              (when (and (aref cyc q) (aref cls q))
                (let ((k (+ (ash q 18) q)))
                  (setf (gethash k reach) t)
                  (push k order))))
            (loop while order
                  do (let* ((node (pop order))
                            (p (ash node -18))
                            (r (logand node #x3ffff))
                            (outs '()))
                       (incf qwork (* (d p) (d r)))
                       (when (> qwork +ax-q-max+) (ax-reject "the analysis would be too large"))
                       (dolist (p2 (aref succ p))
                         (when (= (aref comp p2) (aref comp p))
                           (dolist (r2 (aref succ r))
                             (when (and (= (aref comp r2) (aref comp p))
                                        (ax-intersects-p (aref cls p2) (aref cls r2)))
                               (let ((k2 (+ (ash p2 18) r2)))
                                 (push k2 outs)
                                 (unless (gethash k2 reach)
                                   (setf (gethash k2 reach) t)
                                   (push k2 order)))))))
                       (setf (gethash node fwd) outs))))
          (let ((rev (make-hash-table)) (back (make-hash-table)) (todo '()))
            (maphash (lambda (u outs) (dolist (v outs) (push u (gethash v rev)))) fwd)
            (maphash (lambda (k v) (declare (ignore v))
                       (when (= (ash k -18) (logand k #x3ffff))
                         (setf (gethash k back) t)
                         (push k todo)))
                     reach)
            (loop while todo
                  do (let ((v (pop todo)))
                       (dolist (u (gethash v rev))
                         (unless (gethash u back)
                           (setf (gethash u back) t)
                           (push u todo)))))
            (maphash (lambda (k v) (declare (ignore v))
                       (unless (= (ash k -18) (logand k #x3ffff))
                         (ax-reject "exponential ambiguity")))
                     back)))))))

(defun check-regex-ambiguity (pattern ignore-case pos)
  "SPEC 7.8: refuse a pattern a backtracking engine can run exponentially long on."
  (let ((*ax-pattern* pattern) (*ax-pos* pos))
    (ax-analyse (ax-parse pattern ignore-case))))

(defvar *regex-cache* (make-hash-table :test #'equal))
;;; Keys in insertion order, oldest first: the cache holds at most 256 patterns
;;; (SPEC 7.8) and evicts the oldest, because patterns come from data.
(defvar *regex-cache-order* '())
(defvar *regex-cache-lock* (sb-thread:make-mutex :name "sel regex cache"))
(defconstant +regex-cache-size+ 256)

(defun regex-cache-get (key)
  (sb-thread:with-mutex (*regex-cache-lock*) (gethash key *regex-cache*)))

(defun regex-cache-put (key value)
  (sb-thread:with-mutex (*regex-cache-lock*)
    (unless (nth-value 1 (gethash key *regex-cache*))
      (when (>= (hash-table-count *regex-cache*) +regex-cache-size+)
        (let ((oldest (pop *regex-cache-order*)))
          (remhash oldest *regex-cache*)))
      (setf *regex-cache-order* (nconc *regex-cache-order* (list key))))
    (setf (gethash key *regex-cache*) value)))

(defun check-regex-flags (flags flag-pos)
  "Flags: `i` and nothing else (SPEC 7.8) -- lowercase, so `I` is not `i`, and
neither are the letters whose case-folding lands on it. Returns ignore-case."
  (let ((ignore-case nil))
    (loop for ch across flags
          do (cond
               ((char= ch #\i) (setf ignore-case t))
               ((or (char= ch #\m) (char= ch #\s))
                (fail "E_BAD_ARG"
                      (format nil "flag ~s is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject"
                              (string ch))
                      flag-pos))
               (t (fail "E_BAD_ARG" (format nil "unknown regex flag ~s" (string ch)) flag-pos))))
    ignore-case))

(defun check-regex-pattern (pattern ignore-case flag-pos pat-pos)
  "Everything about a pattern that does not need the engine: the size cap, the
ASCII rule for `i`, the syntax whitelist and the shape rules. Returns nothing;
raises."
  (when (> (length pattern) +limit-max-regex-pattern+)
    (bad-regex (format nil "pattern longer than ~d code points" +limit-max-regex-pattern+)
               pattern 0 pat-pos))
  (when ignore-case
    (loop for ch across pattern
          do (when (> (char-code ch) #x7f)
               (fail "E_BAD_ARG"
                     "the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript"
                     flag-pos))))
  (validate-pattern pattern pat-pos)
  (check-regex-shape pattern pat-pos)
  (check-regex-ambiguity pattern ignore-case pat-pos))

(defun regex-literal-check (pattern flags pattern-pos flag-pos)
  "The compile-time check of a literal pattern (SPEC 7.8): what running the call
would say about the pattern, said when the program is compiled, so a pattern in a
branch that never runs is still refused. FLAGS is NIL when the flags argument is
absent or not a literal."
  ;; An unknown flag, or `i` beside a non-ASCII pattern, is the RUN-TIME E_BAD_ARG
  ;; (SPEC 7.8): here only a valid `i` over an ASCII pattern narrows the analysis, as
  ;; in the other hosts, whose compile-time check reads the flag the same way.
  (let ((ic (and flags (equal flags "i") (every (lambda (c) (< (char-code c) 128)) pattern))))
    (check-regex-pattern pattern ic flag-pos pattern-pos)))

(defun compile-regex (pattern flags flag-pos pat-pos)
  "Returns (values scanner tail-scanner-function ignore-case).

The second value is a function of no arguments that returns the TAIL scanner: the
same pattern with `^` made unsatisfiable, which RREPLACE uses for every match after
the first, because cl-ppcre re-anchors \\A to :start. It is built on the first call
and kept with the cached head scanner: RMATCH, RFIND and RGROUPS never ask for it,
and building it for every pattern doubled the compile cost of a pattern used once."
  (let* ((ignore-case (check-regex-flags flags flag-pos))
         (key (concatenate 'string (if ignore-case "i " " ") pattern))
         (cached (regex-cache-get key)))
    (flet ((build (anchored)
             (let ((source (validate-pattern pattern pat-pos anchored)))
               (handler-case
                   (cl-ppcre:create-scanner source
                                            :case-insensitive-mode ignore-case
                                            :single-line-mode t   ; dotall
                                            :multi-line-mode nil)
                 (cl-ppcre:ppcre-syntax-error (e)
                   (fail "E_REGEX_SYNTAX"
                         (format nil "~a in /~a/" e pattern)
                         pat-pos))))))
      (unless cached
        (check-regex-pattern pattern ignore-case flag-pos pat-pos)
        ;; (head . tail): TAIL is NIL until a RREPLACE continues a scan.
        (setf cached (cons (build t) nil))
        (regex-cache-put key cached))
      (values (car cached)
              (lambda ()
                (or (cdr cached)
                    ;; Two threads may both build it; the scanners are equal and the
                    ;; last store wins, which is harmless.
                    (setf (cdr cached) (build nil))))
              ignore-case))))

;;; cl-ppcre matches by recursion: a repeated group over a long subject nests one
;;; frame set per iteration, and `^(?:ab|a)*$` over 100,000 characters exhausted the
;;; default 2 MB control stack. The answer is a value, not a crash, so a
;;; scan that runs out of stack is run again in a thread with a control stack big
;;; enough for the subjects the language allows; the address space is reserved, not
;;; committed. Only if THAT runs out too is the request refused, as E_RANGE at the
;;; call -- a SEL error, the way every other resource limit is one.
(defconstant +regex-thread-stack+ (* 512 1024 1024))
(defvar *regex-in-big-stack* nil)   ; true inside the big-stack thread
(defvar *regex-stack-users* 0)
(defvar *regex-stack-before* 0)
(defvar *regex-stack-lock* (sb-thread:make-mutex :name "sel regex stack"))

(defun call-in-big-stack (fn)
  ;; SBCL reads `thread_control_stack_size` when it creates a thread AND again when
  ;; it places a running thread's guard pages and when the process exits, so the
  ;; setting must outlive every thread made under it and be put back only after
  ;; the last of them has finished. Putting it back right after MAKE-THREAD made
  ;; the guard-page arithmetic wrong and segfaulted; never putting it back
  ;; segfaulted at exit. Threads that overlap share one setting, counted here.
  (sb-thread:with-mutex (*regex-stack-lock*)
    (when (zerop *regex-stack-users*)
      (setf *regex-stack-before*
            (sb-alien:extern-alien "thread_control_stack_size" sb-alien:unsigned-long))
      (setf (sb-alien:extern-alien "thread_control_stack_size" sb-alien:unsigned-long)
            +regex-thread-stack+))
    (incf *regex-stack-users*))
  (let ((result
          (unwind-protect
               (sb-thread:join-thread
                (sb-thread:make-thread
                 (lambda ()
                   (handler-case (cons :ok (multiple-value-list
                                            (let ((*regex-in-big-stack* t)) (funcall fn))))
                     (storage-condition () (list :exhausted))
                     (error (c) (list :error c))))))
            (sb-thread:with-mutex (*regex-stack-lock*)
              (when (zerop (decf *regex-stack-users*))
                (setf (sb-alien:extern-alien "thread_control_stack_size" sb-alien:unsigned-long)
                      *regex-stack-before*))))))
    (ecase (car result)
      (:ok (values-list (cdr result)))
      (:exhausted (fail "E_RANGE" "the regular expression needs more stack than this host can give it"))
      (:error (error (second result))))))

(defconstant +regex-big-stack-subject+ 4000)

(defun call-with-regex-stack (subject-length fn)
  "Runs FN on the big stack when the subject is long, once: a walk that scans many
times (RREPLACE) pays for one thread, not one per match."
  (if (and (> subject-length +regex-big-stack-subject+) (not *regex-in-big-stack*))
      (call-in-big-stack fn)
      (funcall fn)))

(defun regex-scan (scanner subject &key (start 0))
  "cl-ppcre:scan, with the stack fallback above. A long subject goes straight to
the big stack rather than exhausting the small one first: running out is
recoverable but noisy (the runtime reports the guard page on stderr), and a
subject this long is where it happens."
  (flet ((scan () (cl-ppcre:scan scanner subject :start start)))
    (cond (*regex-in-big-stack* (scan))
          ((> (length subject) +regex-big-stack-subject+) (call-in-big-stack #'scan))
          ;; STORAGE-CONDITION, the standard class SBCL's stack exhaustion
          ;; belongs to, as in CALL-IN-BIG-STACK: no internal SBCL symbol.
          (t (handler-case (scan)
               (storage-condition () (call-in-big-stack #'scan)))))))

(defstruct (rx (:constructor make-rx (scanner subject folded)))
  scanner
  subject    ; the original, which everything is sliced from
  folded)    ; what is actually matched against; same length as SUBJECT

(defun regex-flags (a flag-index)
  "The flags argument at FLAG-INDEX and the position an error in it reports, as
two values: \"\" at the call when the call has none. Evaluates the argument, so
it is called where the evaluation order puts it."
  (if (> (args-count a) flag-index)
      (values (args-text a flag-index) (args-pos-of a flag-index))
      (values "" (args-pos a))))

(defun regex-args (a pat-index subj-index flag-index)
  (multiple-value-bind (pattern subject flags flag-pos)
      (let* ((pattern (args-text a pat-index))
             (subject (args-text a subj-index)))
        (multiple-value-bind (flags flag-pos) (regex-flags a flag-index)
          (values pattern subject flags flag-pos)))
    (multiple-value-bind (scanner tail-scanner ignore-case)
        (compile-regex pattern flags flag-pos (args-pos-of a pat-index))
      (declare (ignore tail-scanner))   ; only RREPLACE continues a scan
      (make-rx scanner subject (if ignore-case (fold-subject subject) subject)))))

(define-builtin "RMATCH" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      (make-bool (and (regex-scan (rx-scanner r) (rx-folded r)) t)))))

(define-builtin "RFIND" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      ;; A CL character is a code point, so the index is already what SEL reports.
      (make-int (let ((at (regex-scan (rx-scanner r) (rx-folded r))))
                  (if at (1+ at) 0))))))

(define-builtin "RGROUPS" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      (multiple-value-bind (start end reg-starts reg-ends)
          (regex-scan (rx-scanner r) (rx-folded r))
        (if (null start)
            (make-none)
            ;; Sliced from the original subject, not the folded one: what comes
            ;; back is the subject's own characters.
            (make-list-value
             (cons (%text (subseq (rx-subject r) start end))
                   (loop for s across reg-starts
                         for e across reg-ends
                         ;; A capture that did not participate yields "".
                         collect (%text (if (and s e) (subseq (rx-subject r) s e) ""))))))))))

;;; Replacement is spliced by hand rather than handed to cl-ppcre, whose
;;; replacement syntax differs from every other host's. SEL understands $0-$9 and
;;; $$ only; every other character is literal.
(defun expand-replacement (repl subject start end reg-starts reg-ends at)
  (let ((group-count (1+ (length reg-starts))))
    (with-output-to-string (out)
      (let ((i 0))
        (loop while (< i (length repl))
              do (let ((c (char repl i)))
                   (if (char/= c #\$)
                       (progn (write-char c out) (incf i))
                       (let ((next (if (< (1+ i) (length repl)) (char repl (1+ i)) nil)))
                         (cond
                           ((and next (char= next #\$)) (write-char #\$ out) (incf i 2))
                           ((and next (ascii-digit-p next))
                            (let ((g (ascii-digit-value next)))
                              (when (>= g group-count)
                                (fail "E_BAD_ARG"
                                      (format nil "replacement refers to $~d but the pattern has ~d groups"
                                              g (1- group-count))
                                      at))
                              (if (zerop g)
                                  (write-string (subseq subject start end) out)
                                  (let ((s (aref reg-starts (1- g)))
                                        (e (aref reg-ends (1- g))))
                                    (when (and s e) (write-string (subseq subject s e) out))))
                              (incf i 2)))
                           (t (write-char #\$ out) (incf i)))))))))))

(define-builtin "RREPLACE" 3 4
  (lambda (a ctx)
    (declare (ignore ctx))
    (let* ((pattern (args-text a 0))
           (repl (args-text a 1))
           (subject (args-text a 2))
           (at (args-pos-of a 1)))
      (multiple-value-bind (flags flag-pos) (regex-flags a (regex-flag-index "RREPLACE"))
        (multiple-value-bind (scanner tail-scanner ignore-case)
            (compile-regex pattern flags flag-pos (args-pos-of a 0))
          (let ((folded (if ignore-case (fold-subject subject) subject)))
            (call-with-regex-stack
             (length folded)
             (lambda ()
               (%text
                (with-output-to-string (out)
                  (let ((last 0)
                        (from 0)
                        (size 0)          ; what has been written so far, for the cap
                        (n (length folded)))
                    (loop
                      (when (> from n) (return))
                      (multiple-value-bind (start end reg-starts reg-ends)
                          (regex-scan (if (zerop from) scanner (funcall tail-scanner))
                                      folded :start from)
                        (when (null start) (return))
                        (let ((expansion (expand-replacement repl subject start end
                                                             reg-starts reg-ends at)))
                          ;; Refused as soon as the result would pass MAX_TEXT_LEN
                          ;; (SPEC 6.4), before more of it is built.
                          (incf size (+ (- start last) (length expansion)))
                          (check-text-cap size (args-pos a))
                          (write-string subject out :start last :end start)
                          (write-string expansion out))
                        (setf last end)
                        ;; Advance a whole code point so a zero-width match cannot loop.
                        (setf from (if (= start end) (1+ start) end))))
                    (write-string (subseq subject (min last (length subject))) out))))))))))))
