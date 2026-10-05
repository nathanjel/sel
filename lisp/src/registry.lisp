;;;; The function table, fixed at load time. SEL has no DEFUN, which is what lets
;;;; an unknown name and a wrong argument count be compile-time errors.

(in-package #:sel)

(defconstant +variadic+ most-positive-fixnum)

(defstruct (spec (:constructor make-spec (name min max lazy binds arity-error fn)))
  (name "" :type string)
  (min 0 :type fixnum)
  (max 0 :type fixnum)
  (lazy nil :type boolean)
  ;; Introduces an element binder; see DEPENDENCIES.
  (binds nil :type boolean)
  ;; Optional extra arity rule, checked after min/max. Returns a message when the
  ;; count is wrong and NIL when it is fine.
  (arity-error nil)
  (fn nil)
  ;; Set once, on every function the library itself defines, when the shipped
  ;; table is sealed (SEAL-SHIPPED-BUILTINS). Everything else -- REGISTER-FUNCTION,
  ;; REGISTER-BUILTIN, a DEFINE-BUILTIN after the seal -- is an application's
  ;; function, which SHIPPED-CALL-P treats as able to do anything.
  (shipped nil :type boolean))

;;; Spec §8.1: registering or replacing a function while other threads compile
;;; or run programs is safe. Readers (the parser, once per call node, never the
;;; evaluator: a call node keeps the spec it was compiled with) go through the
;;; synchronised table; every check-then-write sequence below holds
;;; *REGISTRY-LOCK*, so two registrations cannot interleave either.
(defvar *registry* (make-hash-table :test #'equal :synchronized t))
(defvar *registry-lock* (sb-thread:make-mutex :name "sel function registry"))

;;; Names registered through REGISTER-FUNCTION, which alone may be replaced by
;;; it. Written under *REGISTRY-LOCK*; read by HOST-ARITY.
(defvar *host-functions* (make-hash-table :test #'equal :synchronized t))

;;; The shipped table is authored once, in spec/builtins.json, and rendered into
;;; builtin-manifest.lisp, which fills this list. A name the manifest knows is
;;; held to it: min/max/lazy/binds must agree, and the extra arity rule (COND's
;;; odd count, LINK's three-or-five) comes from the manifest rather than from
;;; the caller -- one body for all five hosts. A name it does not know is a
;;; host's own function (examples/fn-*) and passes.
(defvar *builtin-manifest-data* nil)
(defvar *builtin-form-data* nil)
;; Filled by math-ops.lisp, the rendering of spec/math-ops.json; read by math-plan.lisp.
(defvar *math-op-data* nil)

(defun manifest-entry (upper)
  (assoc upper *builtin-manifest-data* :test #'string=))

(defun manifest-arity-error (rule)
  (destructuring-bind (kind detail message) rule
    (flet ((say (n)
             (let ((at (search "{count}" message)))
               (concatenate 'string (subseq message 0 at)
                            (format nil "~d" n)
                            (subseq message (+ at 7))))))
      (ecase kind
        (:parity (let ((odd (eq detail :odd)))
                   (lambda (n) (unless (eq (oddp n) odd) (say n)))))
        (:allowed (lambda (n) (unless (member n detail) (say n))))))))

(defun define-builtin (name min max fn &key (lazy nil) (binds nil) (arity-error nil))
  "Define one of the library's own builtins, held to its spec/builtins.json
entry. Library-internal: the files under src/builtins/ call it while the system
loads (examples/fn-*/lisp.lisp show where a new one goes). An application adds
functions with REGISTER-FUNCTION, or REGISTER-BUILTIN for lazy and binding
forms; a DEFINE-BUILTIN after the library has loaded is an application's
function like theirs, never a shipped one."
  (let* ((upper (ascii-upcase name))
         (max (or max min))
         (entry (manifest-entry upper)))
    (when entry
      (destructuring-bind (m-min m-max m-lazy m-binds rule) (rest entry)
        (let ((m-max (if (eq m-max :variadic) +variadic+ m-max))
              (wrong '()))
          (unless (= min m-min) (push (format nil "min ~d vs ~d" min m-min) wrong))
          (unless (= max m-max) (push (format nil "max ~d vs ~d" max m-max) wrong))
          (unless (eq (and lazy t) (and m-lazy t)) (push (format nil "lazy ~a vs ~a" lazy m-lazy) wrong))
          (unless (eq (and binds t) (and m-binds t)) (push (format nil "binds ~a vs ~a" binds m-binds) wrong))
          (when arity-error (push "an arity rule of its own, which the manifest owns" wrong))
          (when wrong
            (error "SEL function ~a disagrees with spec/builtins.json: ~{~a~^; ~}" upper (nreverse wrong)))
          (when rule (setf arity-error (manifest-arity-error rule))))))
    (sb-thread:with-mutex (*registry-lock*)
      (when (gethash upper *registry*)
        (error "SEL function ~a defined twice" upper))
      (setf (gethash upper *registry*)
            (make-spec upper min max lazy binds arity-error fn)))))

(defun assert-builtin-manifest-covered ()
  "Called once the shipped builtins have loaded: a manifest entry with no
definition is a host that would silently lack a builtin the others have."
  (let ((missing (loop for entry in *builtin-manifest-data*
                       unless (gethash (first entry) *registry*) collect (first entry))))
    (when missing
      (error "spec/builtins.json names builtins this host never defined: ~{~a~^, ~}" missing))))

(defun seal-shipped-builtins ()
  "Called once, when the library's own builtins have loaded: marks each of them
shipped. An application may add functions and replace its own, never these."
  (sb-thread:with-mutex (*registry-lock*)
    (maphash (lambda (name spec) (declare (ignore name)) (setf (spec-shipped spec) t))
             *registry*)))

(defun function-name-p (name)
  "A well-formed SEL function name: ASCII letters, digits and _, starting with a
letter."
  (and (stringp name)
       (plusp (length name))
       (let ((c (char name 0))) (or (char<= #\A c #\Z) (char<= #\a c #\z)))
       (every (lambda (c) (or (char<= #\A c #\Z) (char<= #\a c #\z)
                              (char<= #\0 c #\9) (char= c #\_)))
              name)))

(defun register-builtin (name min max fn &key (lazy nil) (binds nil) (arity-error nil) (overwrite t))
  "Register (or, with OVERWRITE, redefine) an application's function, lazy or
binding forms included -- the lower-level sibling of REGISTER-FUNCTION. It is
held to the same rules: a well-formed name that is not a reserved word, an arity
0 <= MIN <= MAX, a function to run, and never the name of a builtin the library
ships (`COUNT` cannot become 42 for the whole process). Anything else signals a
plain ERROR. OVERWRITE NIL also refuses the name of an earlier registration."
  (unless (function-name-p name)
    (error "SEL function name must be ASCII letters, digits and _, starting with a letter: ~s" name))
  (let ((upper (ascii-upcase name))
        (max (or max min)))
    (when (reservedp upper)
      (error "~a is a reserved word" upper))
    (unless (and (integerp min) (or (integerp max) (eql max +variadic+)) (<= 0 min)
                 (or (eql max +variadic+) (<= min max)))
      (error "SEL function ~a: arity must be whole numbers with 0 <= min <= max" upper))
    (unless (functionp fn)
      (error "SEL function ~a: fn is not a function" upper))
    (sb-thread:with-mutex (*registry-lock*)
      (let ((old (gethash upper *registry*)))
        (when (and old (spec-shipped old))
          (error "~a is a builtin; a registered function cannot replace it" upper))
        (when (and old (not overwrite))
          (error "SEL function ~a defined twice" upper))
        (setf (gethash upper *registry*)
              (make-spec upper min max lazy binds arity-error fn))))))

(defun unregister-function (name)
  "Remove an application's function (never a shipped one). Internal: the unit
tests use it to leave the process as they found it."
  (let ((upper (ascii-upcase name)))
    (sb-thread:with-mutex (*registry-lock*)
      (let ((old (gethash upper *registry*)))
        (when (and old (not (spec-shipped old)))
          (remhash upper *registry*)
          (remhash upper *host-functions*))))))

(defun registry-lookup (name)
  (gethash (ascii-upcase name) *registry*))

(declaim (inline registry-lookup-canonical))
(defun registry-lookup-canonical (name)
  "REGISTRY-LOOKUP for a name the lexer already spelled in canonical (upper) case:
STRING-UPCASE allocates a fresh string even when nothing changes, once per call
node."
  (gethash name *registry*))

(defun function-names ()
  (sort (sb-ext:with-locked-hash-table (*registry*)
          (loop for k being the hash-keys of *registry* collect k))
        #'string<))

