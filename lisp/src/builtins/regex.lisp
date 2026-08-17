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

The other three hosts get the reversed-bound check free from their engines —
JS, PCRE2 and SRELL all reject {2,1} as a syntax error. cl-ppcre accepts it and
matches nothing, so the check has to be explicit here. That is the general shape
of the risk in this file: every rule the other hosts delegate to their engine has
to be written out, because cl-ppcre is the more permissive of the four."
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
        (count 0))
    (write-char #\[ out)
    (when (and (< i n) (char= (char p i) #\^))
      (write-char #\^ out)
      (incf i))
    (when (and (< (1+ i) n) (char= (char p i) #\[) (char= (char p (1+ i)) #\:))
      (bad-regex "POSIX classes such as [[:alpha:]] are not portable" pattern i pos))
    ;; `]` always closes the class. PCRE treats a leading `]` as a literal while
    ;; ECMAScript reads `[]` as an empty class, so neither spelling is portable —
    ;; write `\]` instead.
    (loop while (< i n)
          do (let ((c (char p i)))
               (cond
                 ((char= c #\])
                  (when (zerop count)
                    (bad-regex "empty character class — write \\] for a literal bracket"
                               pattern start pos))
                  (write-char #\] out)
                  (return-from validate-class
                    (values (get-output-stream-string out) (1+ i))))
                 (t
                  (incf count)
                  (if (char= c #\\)
                      (progn
                        (when (>= (1+ i) n)
                          (bad-regex "trailing backslash in character class" pattern i pos))
                        (let* ((e (char p (1+ i)))
                               (expansion (cdr (assoc e +expand-inside+))))
                          (cond
                            (expansion (write-string expansion out) (incf i 2))
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
;;; thing to every engine. All four hosts run this, so all four compile the same
;;; pattern — apart from the anchor lowering noted at the top of this file.
(defun validate-pattern (pattern pos &optional (anchored-at-start t))
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
                    (write-string (if anchored-at-start "\\A" "[^\\s\\S]") out)
                    (incf i))
                   ((char= c #\$) (write-string "\\z" out) (incf i))

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

(defvar *regex-cache* (make-hash-table :test #'equal))

(defun compile-regex (pattern flags flag-pos pat-pos)
  "Returns (values scanner tail-scanner ignore-case).

TAIL-SCANNER is the same pattern with `^` made unsatisfiable; RREPLACE uses it
for every match after the first, because cl-ppcre re-anchors \\A to :start."
  (let ((ignore-case nil))
    (loop for ch across flags
          for f = (char-downcase ch)
          do (cond
               ((char= f #\i) (setf ignore-case t))
               ((or (char= f #\m) (char= f #\s))
                (fail "E_BAD_ARG"
                      (format nil "flag ~s is not offered — SEL always matches . against any character and anchors ^ $ to the whole subject"
                              (string ch))
                      flag-pos))
               (t (fail "E_BAD_ARG" (format nil "unknown regex flag ~s" (string ch)) flag-pos))))

    (when ignore-case
      (loop for ch across pattern
            do (when (> (char-code ch) #x7f)
                 (fail "E_BAD_ARG"
                       "the i flag needs an ASCII-only pattern — case folding above ASCII differs between PCRE and ECMAScript"
                       flag-pos))))

    (let* ((key (concatenate 'string (if ignore-case "i " " ") pattern))
           (cached (gethash key *regex-cache*)))
      (unless cached
        (setf cached
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
                ;; Validation runs on the anchored build, so a bad pattern fails
                ;; before the second one is ever attempted.
                (let ((head (build t)))
                  (cons head (build nil)))))
        (setf (gethash key *regex-cache*) cached))
      (values (car cached) (cdr cached) ignore-case))))

(defstruct (rx (:constructor make-rx (scanner subject folded)))
  scanner
  subject    ; the original, which everything is sliced from
  folded)    ; what is actually matched against; same length as SUBJECT

(defun regex-args (a pat-index subj-index flag-index)
  (let* ((pattern (args-text a pat-index))
         (subject (args-text a subj-index))
         (flags (if (> (args-count a) flag-index) (args-text a flag-index) ""))
         (flag-pos (if (> (args-count a) flag-index)
                       (args-pos-of a flag-index)
                       (args-pos a))))
    (multiple-value-bind (scanner tail-scanner ignore-case)
        (compile-regex pattern flags flag-pos (args-pos-of a pat-index))
      (declare (ignore tail-scanner))   ; only RREPLACE continues a scan
      (make-rx scanner subject (if ignore-case (fold-subject subject) subject)))))

(define-builtin "RMATCH" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      (make-bool (and (cl-ppcre:scan (rx-scanner r) (rx-folded r)) t)))))

(define-builtin "RFIND" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      ;; A CL character is a code point, so the index is already what SEL reports.
      (make-int (let ((at (cl-ppcre:scan (rx-scanner r) (rx-folded r))))
                  (if at (1+ at) 0))))))

(define-builtin "RGROUPS" 2 3
  (lambda (a ctx)
    (declare (ignore ctx))
    (let ((r (regex-args a 0 1 2)))
      (multiple-value-bind (start end reg-starts reg-ends)
          (cl-ppcre:scan (rx-scanner r) (rx-folded r))
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
           (flags (if (> (args-count a) 3) (args-text a 3) ""))
           (flag-pos (if (> (args-count a) 3) (args-pos-of a 3) (args-pos a)))
           (at (args-pos-of a 1)))
      (multiple-value-bind (scanner tail-scanner ignore-case)
          (compile-regex pattern flags flag-pos (args-pos-of a 0))
        (let ((folded (if ignore-case (fold-subject subject) subject)))
          (%text
           (with-output-to-string (out)
             (let ((last 0)
                   (from 0)
                   (n (length folded)))
               (loop
                 (when (> from n) (return))
                 (multiple-value-bind (start end reg-starts reg-ends)
                     (cl-ppcre:scan (if (zerop from) scanner tail-scanner)
                                    folded :start from)
                   (when (null start) (return))
                   (write-string (subseq subject last start) out)
                   (write-string (expand-replacement repl subject start end
                                                     reg-starts reg-ends at)
                                 out)
                   (setf last end)
                   ;; Advance a whole code point so a zero-width match cannot loop.
                   (setf from (if (= start end) (1+ start) end))))
               (write-string (subseq subject (min last (length subject))) out)))))))))
