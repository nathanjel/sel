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

(defun define-builtin (name min max fn &key (lazy nil) (binds nil) (arity-error nil))
  (let ((upper (string-upcase name)))
    (when (gethash upper *registry*)
      (error "SEL function ~a defined twice" upper))
    (setf (gethash upper *registry*)
          (make-spec upper min (or max min) lazy binds arity-error fn))))

(defun registry-lookup (name)
  (gethash (string-upcase name) *registry*))

(defun function-names ()
  (sort (loop for k being the hash-keys of *registry* collect k) #'string<))
