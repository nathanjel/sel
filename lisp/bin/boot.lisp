;;;; Shared preamble for the scripts in this directory.
;;;;
;;;; They run under `sbcl --non-interactive`, which does not read ~/.sbclrc, so
;;;; Quicklisp is loaded explicitly. The system itself is found through
;;;; asdf:*central-registry* rather than by being installed, so a checkout runs
;;;; without being registered anywhere.

(require :asdf)

(let ((setup (merge-pathnames "quicklisp/setup.lisp" (user-homedir-pathname))))
  (if (probe-file setup)
      (load setup)
      (progn
        (format *error-output*
                "~&Quicklisp not found at ~a.~%~
                 The Lisp implementation depends on cl-ppcre; install Quicklisp~%~
                 (https://www.quicklisp.org) and run (ql:quickload :cl-ppcre).~%"
                setup)
        (sb-ext:exit :code 2))))

;;; This file lives in lisp/bin/, so the system definition is one directory up.
(push (truename (merge-pathnames "../" (directory-namestring *load-truename*)))
      asdf:*central-registry*)

(handler-case
    (let ((*standard-output* (make-broadcast-stream)))   ; quiet the build chatter
      (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang))
  (error (e)
    (format *error-output* "~&cannot load the SEL system: ~a~%" e)
    (sb-ext:exit :code 2)))

(defpackage #:sel-cli
  (:use #:common-lisp)
  (:export #:script-args #:read-text-file #:starts-with #:trim-ws #:split-lines
           #:join-lines #:main))

(in-package #:sel-cli)

(defun script-args ()
  "The arguments after --end-toplevel-options, which is how the wrappers pass
them through SBCL's own option parsing. SBCL leaves the marker in *posix-argv*
on some versions and removes it on others, so handle both."
  (let ((argv sb-ext:*posix-argv*))
    (let ((marker (member "--end-toplevel-options" argv :test #'string=)))
      (if marker
          (rest marker)
          ;; No marker left: everything after the last option SBCL understands is
          ;; ours. The wrappers only ever pass file paths and --show.
          (remove-if (lambda (a)
                       (or (string= a "--noinform")
                           (string= a "--disable-debugger")
                           (string= a "--non-interactive")
                           (string= a "--load")
                           (string= a "--eval")
                           (search ".lisp" a)
                           (search "(sel-cli:main)" a)))
                     (rest argv))))))

(defun read-text-file (path)
  (with-open-file (in path :external-format :utf-8)
    (let ((s (make-string (file-length in))))
      (subseq s 0 (read-sequence s in)))))

;;; --- small text helpers shared by the scripts ------------------------------

(defun starts-with (prefix s)
  (and (>= (length s) (length prefix)) (string= prefix s :end2 (length prefix))))

(defun trim-ws (s) (string-trim '(#\Space #\Tab #\Return #\Newline) s))

(defun split-lines (text)
  (let ((lines '())
        (start 0))
    (loop for i from 0 below (length text)
          when (char= (char text i) #\Newline)
            do (push (subseq text start i) lines)
               (setf start (1+ i)))
    (push (subseq text start) lines)
    (nreverse lines)))

(defun join-lines (reversed-lines)
  "Joins lines that were accumulated with PUSH, so in reverse order."
  (format nil "~{~a~^~%~}" (reverse reversed-lines)))
