;;;; Compiles the SEL systems from a clean cache with plain ASDF, the way a user's
;;;; (asdf:load-system "sel-lang") does, and fails on any full compiler WARNING.
;;;;
;;;; Quicklisp's QUICKLOAD muffles such warnings, and every other script here
;;;; loads through it, so a WARNING could sit in the tree while ASDF's
;;;; COMPILE-FILE-ERROR stopped every user who loads the system without
;;;; Quicklisp -- which is what a DEFUN clobbering a structure accessor once did.
;;;; The fasls go to the throwaway directory in $SEL_LOAD_OUT (lisp/bin/check-load
;;;; makes and removes it), so the run neither reads nor disturbs the normal cache.

(require :asdf)
(load (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname)))

(let ((root (truename (merge-pathnames "../" (directory-namestring *load-truename*))))
      (out (sb-ext:posix-getenv "SEL_LOAD_OUT")))
  (unless (and out (plusp (length out)))
    (format *error-output* "~&check-load.lisp: run it through lisp/bin/check-load~%")
    (sb-ext:exit :code 2))
  (push root asdf:*central-registry*)
  (asdf:initialize-output-translations
   `(:output-translations
     (,(merge-pathnames "**/*.*" root)
      ,(merge-pathnames "**/*.*" (uiop:ensure-directory-pathname out)))
     :inherit-configuration)))

;;; A full WARNING is what makes COMPILE-FILE report failure, so it fails the
;;; check. Style warnings are counted, not fatal; ASDF's own summary of them
;;; (UIOP:COMPILE-WARNED-WARNING) is a WARNING too and is not counted twice.
(defvar *warnings* '())
(defvar *style-warnings* '())

(defun describe-condition (w)
  (format nil "~@[~a: ~]~a"
          (and *compile-file-pathname* (file-namestring *compile-file-pathname*))
          (substitute #\Space #\Newline (princ-to-string w))))

(handler-case
    (handler-bind ((warning
                     (lambda (w)
                       (cond ((typep w 'uiop:compile-warned-warning))
                             ((typep w 'style-warning) (push (describe-condition w) *style-warnings*))
                             (t (push (describe-condition w) *warnings*))))))
      (let ((asdf:*compile-file-failure-behaviour* :error)
            (*standard-output* (make-broadcast-stream))
            (*error-output* (make-broadcast-stream)))
        (asdf:load-system "sel-lang/tests")))
  (error (e)
    (format t "~&clean ASDF load failed: ~a~%" (substitute #\Space #\Newline (princ-to-string e)))
    (dolist (w (reverse *warnings*)) (format t "  WARNING ~a~%" w))
    (sb-ext:exit :code 1)))

(when *warnings*
  (dolist (w (reverse *warnings*)) (format t "  WARNING ~a~%" w))
  (sb-ext:exit :code 1))
(format t "~&clean ASDF load of sel-lang, sel-lang/sql and sel-lang/tests: ok~
           ~[~:;, ~:*~d style warning~:p~]~%"
        (length *style-warnings*))
(dolist (w (reverse *style-warnings*)) (format t "  style: ~a~%" w))
