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
  (fn nil))

(defvar *registry* (make-hash-table :test #'equal))

;;; The shipped table is authored once, in spec/builtins.json, and rendered into
;;; builtin-manifest.lisp, which fills this list. A name the manifest knows is
;;; held to it: min/max/lazy/binds must agree, and the extra arity rule (COND's
;;; odd count, LINK's three-or-five) comes from the manifest rather than from
;;; the caller -- one body for all five hosts. A name it does not know is a
;;; host's own function (examples/fn-*) and passes.
(defvar *builtin-manifest-data* nil)

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
  (let* ((upper (string-upcase name))
         (max (or max min))
         (entry (manifest-entry upper)))
    (when (gethash upper *registry*)
      (error "SEL function ~a defined twice" upper))
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
    (setf (gethash upper *registry*)
          (make-spec upper min max lazy binds arity-error fn))))

(defun assert-builtin-manifest-covered ()
  "Called once the shipped builtins have loaded: a manifest entry with no
definition is a host that would silently lack a builtin the others have."
  (let ((missing (loop for entry in *builtin-manifest-data*
                       unless (gethash (first entry) *registry*) collect (first entry))))
    (when missing
      (error "spec/builtins.json names builtins this host never defined: ~{~a~^, ~}" missing))))

(defun register-builtin (name min max fn &key (lazy nil) (binds nil) (arity-error nil) (overwrite t))
  "Register or redefine a custom builtin function in the SEL runtime."
  (let ((upper (string-upcase name)))
    (when (and (not overwrite) (gethash upper *registry*))
      (error "SEL function ~a defined twice" upper))
    (setf (gethash upper *registry*)
          (make-spec upper min (or max min) lazy binds arity-error fn))))

(defun registry-lookup (name)
  (gethash (string-upcase name) *registry*))

(defun function-names ()
  (sort (loop for k being the hash-keys of *registry* collect k) #'string<))

