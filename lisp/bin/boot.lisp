;;;; Shared preamble for the scripts in this directory.
;;;;
;;;; They run under `sbcl --non-interactive`, which does not read ~/.sbclrc, so
;;;; Quicklisp is loaded explicitly. The system itself is found through
;;;; asdf:*central-registry* rather than by being installed, so a checkout runs
;;;; without being registered anywhere.

(require :asdf)

;;; SIGTERM ends the process at once, as it does every other host's CLI. SBCL's
;;; own handler runs EXIT -- unwinding, then joining every thread -- in whichever
;;; thread the kernel hands the signal to. When that is the finalizer thread
;;; (the kernel's choice while the main thread has signals deferred, e.g. during
;;; a GC), its EXIT takes the exit lock and the thread ends holding it, and the
;;; main thread's EXIT then waits for that lock forever; a second SIGTERM (a
;;; `timeout` signals its child and then its whole process group) deadlocks the
;;; two EXITs against each other. Either way `timeout` could not stop a runner.
;;; Nothing here has cleanup that a terminated run could still use.
(sb-sys:enable-interrupt sb-unix:sigterm :default)

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
  (:export #:script-args #:read-text-file #:read-file-or-exit #:unreadable-file
           #:read-corpus #:escape-newlines #:render #:no-cases
           #:reset-probes #:say #:yn #:print-probes
           #:starts-with #:trim-ws #:split-lines #:join-lines #:main))

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

(define-condition unreadable-file (error)
  ((path :initarg :path :reader unreadable-file-path))
  (:report (lambda (c s) (format s "cannot read ~a" (unreadable-file-path c)))))

(defun read-text-file (path)
  "The text of PATH: its bytes, decoded as UTF-8 with no newline translation of
any kind (a CR is program text). Signals UNREADABLE-FILE for a file that is
missing, unreadable, or a directory."
  (let ((octets
          (handler-case
              (progn
                (when (or (uiop:directory-exists-p path)
                          (not (probe-file path)))
                  (error 'unreadable-file :path path))
                (with-open-file (in path :element-type '(unsigned-byte 8))
                  (let ((buf (make-array (file-length in) :element-type '(unsigned-byte 8))))
                    (subseq buf 0 (read-sequence buf in)))))
            (file-error () (error 'unreadable-file :path path))
            (stream-error () (error 'unreadable-file :path path)))))
    (sb-ext:octets-to-string octets :external-format :utf-8)))

(defun read-file-or-exit (path)
  "READ-TEXT-FILE, or one line \"cannot read PATH\" on stderr and status 1."
  (handler-case (read-text-file path)
    (unreadable-file (e)
      (format *error-output* "~a~%" e)
      (finish-output *error-output*)
      (sb-ext:exit :code 1 :abort t))))

(defun no-cases (control &rest args)
  "A run that executed nothing proves nothing: say so, one line, and exit 1."
  (format *error-output* "~?~%" control args)
  (finish-output *error-output*)
  (sb-ext:exit :code 1 :abort t))

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

;;; --- the corpus format (tools/README.md) --------------------------------------

(defun strip-final-newline (s)
  (let ((n (length s)))
    (if (and (plusp n) (char= (char s (1- n)) #\Newline))
        (subseq s 0 (1- n))
        s)))

(defun read-corpus (text)
  "The records of a corpus: a line beginning `### ` starts one, and everything
after it is source until the next marker. Split on LF and nothing else; a
record is its joined lines with exactly one trailing LF removed (never a CR,
never a second LF)."
  (let ((records '())
        (current nil)
        (started nil))
    (dolist (line (split-lines text))
      (if (starts-with "### " line)
          (progn
            (when started (push (strip-final-newline (join-lines current)) records))
            (setf current '() started t))
          (when started (push line current))))
    (when started (push (strip-final-newline (join-lines current)) records))
    (nreverse records)))

(defun escape-newlines (s)
  "One line per program is the protocol: a value holding a newline must not
desynchronise the comparison."
  (with-output-to-string (out)
    (loop for c across s
          do (if (char= c #\Newline) (write-string "\\n" out) (write-char c out)))))

(defun render (v)
  "The rendering of bin/sel (and of batch --show): a scalar as itself, BIN as
bin:HEX, anything with children as its dump."
  (if (zerop (sel:value-size v))
      (case (sel:value-kind v)
        (:text (sel::value-scalar v))
        (:bool (sel:value-dump v))
        (:bin (concatenate 'string "bin:" (subseq (sel:value-dump v) 1)))
        (t (sel:value-dump v)))
      (sel:value-dump v)))

;;; --- the API parity probes (tools/api.mjs, tools/check-sqlapi.sh) -----------
;;; One numbered "NN name = value" line per probe; the hosts' reports are diffed.

(defvar *probes* '())
(defvar *probe-n* 0)

(defun reset-probes () (setf *probes* '() *probe-n* 0))

(defun say (name value)
  (push (format nil "~2,'0d ~a = ~a" (incf *probe-n*) name value) *probes*))

(defun yn (x) (if x "true" "false"))

(defun print-probes () (format t "~{~a~%~}" (reverse *probes*)))
