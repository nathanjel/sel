;;;; Dialect lookup, and the runtime registration an application extends the map
;;;; with.
;;;;
;;;; The generated table in map-data.lisp is already flattened, so a shipped
;;;; lookup is one ASSOC; the overlay written here is what re-introduces the
;;;; extends chain, and it is the only thing that does.

(in-package #:sel.sql)

(defparameter +sections+ '(:ops :funcs :skel))

;;; Dialects declared at run time, and runtime entries consulted before the
;;; generated table. Both are ALISTS rather than hash tables: an aggregate's
;;; elements must unroll in the order they were written, and the same shape is
;;; used throughout so no lookup path has a different one.
(defvar *extra* '())
(defvar *overlay* '())        ; ((dialect . ((section . ((key . entry) ...)) ...)) ...)

;;; Every key DEFINE-DIALECT accepts. sql/MAP.md §3 is the normative list.
(defparameter +dialect-keys+ '(:extends :version :target :lexical))

(defun check-section (section)
  (unless (member section +sections+)
    (bad "unknown map section ~s; use ~{~s~^, ~}" section +sections+)))

(defun map-reset ()
  "Forget every runtime registration. For tests; nothing else should need it."
  (setf *extra* '() *overlay* '())
  (values))

;;; --- lookup ---------------------------------------------------------------

(defun dialect-record (name)
  (or (cdr (assoc name *extra* :test #'equal))
      (cdr (assoc name +dialects+ :test #'equal))))

(defun dialect-exists-p (name)
  (and (dialect-record name) t))

(defun dialect-targets ()
  "Every dialect that may be named in a TRANSLATE call, sorted."
  (let ((out '()))
    (dolist (row +dialects+)
      (when (getf (cdr row) :target) (pushnew (car row) out :test #'equal)))
    (dolist (row *extra*)
      (when (getf (cdr row) :target) (pushnew (car row) out :test #'equal)))
    (sort out #'string<)))

(defun require-target (name &optional pos)
  "A base is not a target: `ansi` and `mysql-family` name no server anyone runs,
and a dialect no database implements is not one a caller should aim at."
  (let ((rec (dialect-record name)))
    (unless rec
      (refuse "E_SQL_DIALECT"
              (format nil "there is no SQL dialect ~a; known targets are ~{~a~^, ~}"
                      name (dialect-targets))
              pos))
    (unless (getf rec :target)
      (refuse "E_SQL_DIALECT"
              (format nil "~a is a base other dialects inherit from, not a server ~
anyone runs; translate to one of ~{~a~^, ~}" name (dialect-targets))
              pos))))

(defun dialect-chain (name)
  "Self first, then extends, up to the root."
  (let ((out '())
        (cur name))
    (loop
      ;; A runtime dialect could extend one that extends it back, and a lookup
      ;; must answer rather than spin.
      (when (or (null cur) (member cur out :test #'equal)) (return))
      (let ((rec (dialect-record cur)))
        (unless rec (return))
        (push cur out)
        (setf cur (getf rec :extends))))
    (nreverse out)))

(defun dialect-version (name)
  (let ((rec (dialect-record name)))
    (unless rec (bad "SQL dialect ~a does not exist" name))
    (getf rec :version)))

(defun dialect-lexical (name key)
  "A lexical value, or NIL when nothing in the chain supplies one.

PRESENCE decides the walk, not nullness: sql/MAP.md §3 gives a null lexical
value the job of REFUSING -- a null binaryLiteral refuses BIN literals -- and
reading it as absent walks on to the base and hands the withdrawn value back.
The two answer the same NIL to the caller, exactly as the other hosts do; what
differs is that a withdrawal stops here."
  (dolist (d (dialect-chain name) nil)
    (let* ((rec (dialect-record d))
           (cell (assoc key (getf rec :lexical) :test #'equal)))
      (when cell (return (cdr cell))))))

(defun dialect-entry (name section key)
  "One entry, as (VALUES entry foundp).

Two values rather than a sentinel, because NIL is a legitimate entry -- a
refusal without a reason -- so it cannot also mean `not found`. Python spells
the same distinction with a MISSING string it compares by identity.

The whole overlay chain first, and only then the generated table. Interleaving
the two per level would look tidier and would be wrong: the generated tables are
already flattened, so a generated hit at the leaf would shadow a runtime entry
registered against a base, and registering against `ansi` is documented to reach
every dialect."
  (check-section section)
  (let ((chain (dialect-chain name)))
    (dolist (d chain)
      (let* ((sec (cdr (assoc section (cdr (assoc d *overlay* :test #'equal)))))
             (cell (assoc key sec :test #'equal)))
        (when cell (return-from dialect-entry (values (cdr cell) t)))))
    (dolist (d chain)
      (let* ((rec (cdr (assoc d +dialects+ :test #'equal)))
             (cell (assoc key (getf rec section) :test #'equal)))
        (when cell (return-from dialect-entry (values (cdr cell) t)))))
    (values nil nil)))

;;; --- versions -------------------------------------------------------------

(defun split-dots (s)
  (let ((out '()) (start 0))
    (loop for i from 0 below (length s)
          when (char= (char s i) #\.)
            do (push (subseq s start i) out) (setf start (1+ i)))
    (push (subseq s start) out)
    (nreverse out)))

(defun dotted-p (s)
  "`[0-9]+(\\.[0-9]+)*`, and nothing cleverer -- sql/MAP.md §4.5."
  (and (stringp s) (plusp (length s))
       (every (lambda (part) (and (plusp (length part))
                                  (every #'ascii-digit-p part)))
              (split-dots s))))

(defun version-at-least (have want)
  (let ((a (mapcar #'parse-integer (split-dots have)))
        (b (mapcar #'parse-integer (split-dots want))))
    (loop for i from 0 below (max (length a) (length b))
          for x = (or (nth i a) 0)
          for y = (or (nth i b) 0)
          unless (= x y) do (return-from version-at-least (> x y)))
    t))

;;; --- registration ---------------------------------------------------------
;;;
;;; What tools/gen-sql-map.mjs enforces at generation time, enforced here at
;;; registration time, against the vocabulary that file EMITS rather than a
;;; second copy of it. Every refusal below closes a place where the hosts
;;; improvised differently over an entry the generator would never have accepted
;;; -- a list where a template belongs, an arity of strings, a `ret` that was
;;; not there at all.
;;;
;;; All of them signal a plain ERROR, never a SQL-ERROR: a malformed
;;; registration is a mistake in the application's startup, and TRY-TRANSLATE
;;; must not swallow it.

(defparameter +absent+ '#:absent
  "Distinguishes a plist key that is absent from one whose value is NIL. That is
the whole of sql/MAP.md §2's rule about withdrawal, and GETF alone cannot say
it.")

(defun plist-get (plist key) (getf plist key +absent+))
(defun presentp (v) (not (eq v +absent+)))

(defun rule (key) (getf +rules+ key))

(defun check-lexical (key value where)
  (let ((type (cdr (assoc key (rule :lexical-types) :test #'equal))))
    (unless type
      (bad "~a sets the unknown lexical key ~a; known keys are ~{~a~^, ~}"
           where key (mapcar #'car (rule :lexical-types))))
    ;; NIL is a WITHDRAWAL everywhere in the map, so it is always allowed --
    ;; sql/MAP.md §3 says a null binaryLiteral refuses BIN literals, and
    ;; DIALECT-LEXICAL looks keys up by presence so that it can.
    (when (null value) (return-from check-lexical))
    (ecase type
      (:map
       ;; textEscape given as a STRING made two hosts skip escaping entirely and
       ;; emit 'it's' unquoted. That is an injection, it was in both, and
       ;; nothing checked.
       (unless (and (listp value) (every #'consp value))
         (bad "~a sets ~a to something that is not a map of character to ~
replacement" where key))
       (dolist (cell value)
         (when (or (zerop (length (car cell))) (not (stringp (cdr cell))))
           (bad "~a's ~a maps ~s to something that is not a string" where key (car cell)))))
      (:string
       ;; Never cast to one: `true` given as a boolean rendered as 1 on the PHP
       ;; host and True on Python.
       (unless (stringp value)
         (bad "~a sets ~a to something that is not a string" where key))
       ;; A quote character that is not a character cannot quote. Left through,
       ;; the hosts disagreed about what it meant. textCollate is legitimately
       ;; empty (ansi and sqlite ship it that way); these two are not.
       (when (and (zerop (length value)) (member key '("identQuote" "textQuote") :test #'equal))
         (bad "~a sets ~a to the empty string; a quote character that is not a ~
character cannot quote" where key))))))

(defun define-dialect (name spec)
  "Declare a dialect.

The usual reason is an older or newer server than the shipped map assumes, which
needs no special code because a version is only another link in the chain:

    (define-dialect \"mariadb-11.8\" '(:extends \"mariadb\" :version \"11.8\"))"
  (when (dialect-exists-p name)
    (bad "SQL dialect ~a is already defined; a name means one dialect" name))
  (let ((where (format nil "SQL dialect ~a" name)))
    ;; The keys a dialect declaration carries, and nothing else. :ops, :funcs and
    ;; :skel are NOT among them -- they are defined one entry at a time -- and
    ;; passing them here used to be accepted and silently dropped, which is a
    ;; registration that looks like it worked.
    (loop for (k nil) on spec by #'cddr
          unless (member k +dialect-keys+)
            do (bad "~a declares ~s, which a dialect declaration does not carry; ~
ops, funcs and skel entries are defined one at a time with DEFINE-ENTRY" where k))
    ;; Present-and-NIL, not absent. A dialect with no parent is a real thing --
    ;; `ansi` is one -- but forgetting the key is a typo, and the two must not
    ;; look alike: without this a missing :extends would quietly produce a root
    ;; that inherits nothing and answers every lookup with nothing found.
    (let ((extends (plist-get spec :extends)))
      (unless (presentp extends)
        (bad "~a must say what it extends; write :extends nil for a dialect with ~
no parent, as ansi has" where))
      (when (and extends (not (dialect-exists-p extends)))
        (bad "~a extends ~a, which does not exist" where extends))
      (let* ((raw-version (plist-get spec :version))
             (version (cond ((and (presentp raw-version) raw-version) raw-version)
                            ;; A root inherits nothing, so it has to state its own.
                            ((null extends)
                             (bad "~a extends nothing, so it must declare a ~
version; there is none to inherit" where))
                            (t (getf (dialect-record extends) :version))))
             (raw-target (plist-get spec :target))
             (target (if (presentp raw-target)
                         (progn
                           ;; Refused rather than coerced: truthiness differs
                           ;; between hosts and must not decide this.
                           (unless (member raw-target '(t nil))
                             (bad "~a has a target that is not a boolean; ~
truthiness differs between hosts and must not decide this" where))
                           raw-target)
                         t))
             (lexical (let ((l (plist-get spec :lexical)))
                        (if (presentp l) l '()))))
        ;; Dotted-numeric, as sql/MAP.md §4.5 says. A live server reports
        ;; "11.8.8-MariaDB", which is the natural thing to pass and is not a
        ;; version this map can compare; refused here, at the line that wrote it.
        (unless (dotted-p version)
          (bad "~a has version ~s, which is not dotted-numeric; strip any suffix ~
a server reports (11.8.8-MariaDB is 11.8.8)" where version))
        (unless (listp lexical)
          (bad "~a has a lexical that is not a map" where))
        (dolist (cell lexical)
          (check-lexical (car cell) (cdr cell) where))
        (setf *extra*
              (append *extra*
                      (list (cons name (list :extends extends :version version
                                             :target target :lexical lexical))))))))
  (values))

(defun check-key (section key)
  (case section
    (:ops (unless (assoc key (rule :op-arity) :test #'equal)
            (bad "~a is not a SEL operator, so an ops entry for it would never ~
be looked up" key)))
    ;; `funcs` keys are SEL function names and case-insensitive; ops and skel
    ;; keys are looked up verbatim, which is why DEFINE-ENTRY upper-cases only
    ;; the first. Registering `and` or `Case` used to be silently dead.
    (:funcs (unless (assoc (sel::ascii-upcase key) (rule :func-arity) :test #'equal)
              (bad "~a is not a SEL function this layer maps; the aggregates and ~
IF/COND/COUNT/HAS/INDEXES/ABORT are lowered by stage 2 and never reach the funcs ~
table" key)))
    (:skel (unless (assoc key (rule :skel-slots) :test #'equal)
             (bad "~a is not a skeleton; known ones are ~{~a~^, ~}"
                  key (mapcar #'car (rule :skel-slots)))))))

(defun template-slots (tpl)
  "Every {slot} in a template, in order. The renderer reads them the same way."
  (let ((out '()) (i 0) (n (length tpl)))
    (loop while (< i n)
          do (if (char= (char tpl i) #\{)
                 (let ((end (position #\} tpl :start (1+ i))))
                   (if end
                       (progn (push (subseq tpl (1+ i) end) out) (setf i (1+ end)))
                       (return)))
                 (incf i)))
    (nreverse out)))

(defun count-key-p (s)
  "`0|[1-9][0-9]{0,2}` -- an argument count a template may be keyed by."
  (and (stringp s)
       (or (equal s "0")
           (and (<= 1 (length s) 3)
                (char<= #\1 (char s 0) #\9)
                (every #'ascii-digit-p s)))))

(defun unify-ret-p (s)
  (and (stringp s) (> (length s) 7) (string= "@unify:" s :end2 7)
       (let ((rest (subseq s 7)))
         (and (plusp (length rest))
              (every (lambda (part) (and (plusp (length part))
                                         (every #'ascii-digit-p part)))
                     (let ((out '()) (start 0))
                       (loop for i from 0 below (length rest)
                             when (char= (char rest i) #\,)
                               do (push (subseq rest start i) out) (setf start (1+ i)))
                       (push (subseq rest start) out)
                       (nreverse out)))))))

(defun check-entry (section key entry)
  (let ((where (format nil "the ~(~a~) entry for ~a" section key)))
    ;; A string is a refusal carrying its reason; NIL is a refusal without one.
    ;; Both are entries, and neither has anything else to check.
    (when (or (null entry) (stringp entry)) (return-from check-entry))
    (unless (listp entry)
      (bad "~a must be a plist, a string or NIL" where))
    (let ((builder (plist-get entry :builder)))
      (when (and (presentp builder) builder)
        (unless (functionp builder)
          (bad "~a has a builder that is not a function; use DEFINE-BUILDER" where))
        (return-from check-entry)))

    (let ((caveat (plist-get entry :caveat)))
      (when (and (presentp caveat) caveat)
        (unless (member caveat (rule :caveats) :test #'equal)
          (bad "~a declares the caveat ~s, which is not on the closed list in ~
sql/MAP.md §4.6; a caveat an application cannot branch on is prose" where caveat))))

    ;; A skeleton is a template with NAMED slots and no kind: the translator
    ;; decides what a CASE or a subquery yields, not the map. So it is checked
    ;; for its slots and nothing else.
    (when (eq section :skel)
      (let ((tpl (plist-get entry :tpl)))
        (unless (stringp tpl) (bad "~a needs a tpl that is a string" where))
        (let ((allowed (cdr (assoc key (rule :skel-slots) :test #'equal))))
          (dolist (slot (template-slots tpl))
            (unless (member slot allowed :test #'equal)
              (bad "~a uses the slot {~a}; ~a has ~{~a~^, ~} — a typo would ~
survive as literal text in every query" where slot key allowed)))))
      (return-from check-entry))

    (let ((tpl (plist-get entry :tpl))
          (variants (plist-get entry :variants)))
      (unless (eq (presentp tpl) (not (presentp variants)))
        (bad "~a needs exactly one of :tpl and :variants" where))
      (let ((ret (plist-get entry :ret)))
        (unless (and (presentp ret) (stringp ret)
                     (or (member ret (rule :ret-kinds) :test #'equal)
                         (equal ret "@concat")
                         (unify-ret-p ret)))
          (bad "~a has ret ~s; use one of ~{~a~^, ~}, @concat or @unify:<n>[,<n>...]"
               where (and (presentp ret) ret) (rule :ret-kinds))))
      (let ((since (plist-get entry :since)))
        (when (and (presentp since) since (not (dotted-p since)))
          (bad "~a has a since that is not dotted-numeric" where)))
      (let ((arity (plist-get entry :arity)))
        (when (and (presentp arity) arity)
          (unless (and (consp arity) (integerp (car arity)) (integerp (cdr arity))
                       (>= (car arity) 0) (>= (cdr arity) (car arity)))
            (bad "~a has an arity that is not (min . max) of two integers" where))))

      (when (presentp variants)
        (unless (and (listp variants) variants (every #'consp variants))
          (bad "~a has variants that are not a map" where))
        (let ((allowed (assoc key (rule :variants) :test #'equal)))
          (unless allowed
            (bad "~a uses variants, and ~a is not a variant family" where key))
          (dolist (cell variants)
            (unless (member (car cell) (cdr allowed) :test #'equal)
              (bad "~a declares the variant ~a; ~a has ~{~a~^, ~}"
                   where (car cell) key (cdr allowed))))))

      ;; Every key is an argument COUNT the entry can actually be called with,
      ;; checked against SEL's own arity narrowed by the entry's. A template
      ;; keyed by a count the entry can never be called with could never be
      ;; chosen, and saying so at registration is the difference between a typo
      ;; and a query that silently takes the wrong arm.
      (when (and (presentp tpl) (listp tpl) tpl)
        (let* ((sel-arity (cdr (if (eq section :ops)
                                   (assoc key (rule :op-arity) :test #'equal)
                                   (assoc (sel::ascii-upcase key) (rule :func-arity)
                                          :test #'equal))))
               (lo (car sel-arity))
               (hi (cdr sel-arity))
               (arity (plist-get entry :arity)))
          (when (and (presentp arity) arity)
            (setf lo (max lo (car arity))
                  hi (if hi (min hi (cdr arity)) (cdr arity))))
          (dolist (cell tpl)
            (let ((k (car cell)))
              (unless (equal k "*")
                (unless (count-key-p k)
                  (bad "~a keys a template by ~s; an arity-keyed template uses a ~
count or *" where k))
                (let ((c (parse-integer k)))
                  (when (or (< c lo) (and hi (> c hi)))
                    (bad "~a keys a template by ~a, and ~a takes ~a to ~a ~
argument(s), so that template could never be chosen"
                         where c key lo (or hi "any"))))))))))))

(defun define-entry (dialect section key entry)
  "Define or withdraw one entry.

Unlike SEL's own registry, redefinition is allowed and the last writer wins: a
duplicate SEL function is always a bug, while a duplicate SQL entry is usually
an application deliberately overriding a shipped default for its own schema.

A string withdraws the entry and becomes the reason the caller is given; NIL
withdraws it without one."
  (check-section section)
  (unless (dialect-exists-p dialect) (bad "SQL dialect ~a does not exist" dialect))
  (check-key section key)
  (check-entry section key entry)
  ;; Only `funcs` keys are SEL function names, which are case-insensitive. `ops`
  ;; keys are operator tokens and `skel` keys are camel-case names the
  ;; translator looks up verbatim -- upper-casing those stored a registered
  ;; skeleton under a key nothing ever reads.
  (let* ((k (if (eq section :funcs) (sel::ascii-upcase key) key))
         (dcell (or (assoc dialect *overlay* :test #'equal)
                    (let ((c (cons dialect '()))) (push c *overlay*) c)))
         (scell (or (assoc section (cdr dcell))
                    (let ((c (cons section '())))
                      (setf (cdr dcell) (append (cdr dcell) (list c))) c)))
         (existing (assoc k (cdr scell) :test #'equal)))
    (if existing
        (setf (cdr existing) entry)
        (setf (cdr scell) (append (cdr scell) (list (cons k entry))))))
  (values))

(defun define-builder (dialect section key fn)
  "The escape hatch, for what a template cannot say. A builder receives the
already-rendered arguments and returns a fragment."
  (define-entry dialect section key (list :builder fn)))
