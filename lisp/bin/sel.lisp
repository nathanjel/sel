;;;; SEL command line: evaluate an expression, a file, or start a REPL. The
;;;; interface is the one every host's `sel` has (docs/usage/repl.md):
;;;;
;;;;   sel -e 'EXPR'          evaluate and print
;;;;   sel file.sel           evaluate a file
;;;;   sel --deps -e 'EXPR'   print the variables the expression reads, one per line
;;;;   sel --help, -h         usage;  sel --version  the package version
;;;;   sel                    REPL, keeping one context across lines
;;;;   sel --functions        (this host only) list the function table
;;;;
;;;; A misused command line exits 2 with "sel: ..." on stderr; a file that cannot
;;;; be read exits 1 with "sel: cannot read PATH"; a SEL error exits 1.

(in-package #:sel-cli)

(defun report (e)
  (format *error-output* "~a at line ~d column ~d: ~a~%"
          (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e)
          (sel:sel-error-message e)))

;;; --- source is bytes (spec/SPEC.md section 2) -------------------------------

(defun base64-octets (text)
  "Standard base64 of ASCII TEXT, as octets."
  (let ((out (make-array 0 :element-type '(unsigned-byte 8) :adjustable t :fill-pointer 0))
        (acc 0) (bits 0))
    (loop for ch across text
          for v = (position ch "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/")
          when v
            do (setf acc (logior (ash acc 6) v))
               (incf bits 6)
               (when (>= bits 8)
                 (decf bits 8)
                 (vector-push-extend (ldb (byte 8 bits) acc) out)
                 (setf acc (ldb (byte bits 0) acc))))
    (coerce out '(simple-array (unsigned-byte 8) (*)))))

(defun argument-octets (arg)
  "The octets of one command-line argument: the wrapper sends them as b64:...
so that SBCL never decodes them; anything else (a direct run) is encoded here."
  (if (starts-with "b64:" arg)
      (base64-octets (subseq arg 4))
      (sel::encode-utf8 arg)))

(defun octets-ascii (octets)
  "OCTETS as a string when they are all ASCII, else NIL -- for flags and paths."
  (and (every (lambda (b) (< b 128)) octets)
       (map 'string #'code-char octets)))

(defun octets-path (octets)
  "A file name from octets: UTF-8 where valid, latin-1 otherwise, so a name that is
not UTF-8 is still openable by SBCL's own (utf-8 with replacement) path handling."
  (handler-case (sel::decode-utf8 octets)
    (sel:sel-error () (map 'string #'code-char octets))))

(defun read-file-octets (path)
  (with-open-file (in path :element-type '(unsigned-byte 8))
    (let ((buf (make-array (file-length in) :element-type '(unsigned-byte 8))))
      (subseq buf 0 (read-sequence buf in)))))

(defun source-text (octets)
  "Program text from source octets, or E_UTF8 at the first invalid unit."
  (sel::decode-utf8 octets nil t))

(defun read-octet-line (in)
  "One line of octets from the binary stream IN, without its LF; NIL at eof."
  (let ((line (make-array 0 :element-type '(unsigned-byte 8) :adjustable t :fill-pointer 0))
        (any nil))
    (loop for b = (read-byte in nil nil)
          do (cond ((null b) (return))
                   (t (setf any t)
                      (when (= b 10) (return))
                      (vector-push-extend b line))))
    (and any (coerce line '(simple-array (unsigned-byte 8) (*))))))

(defun usage-error (control &rest args)
  "A misused command line: one plain line on stderr, status 2, nothing on stdout
(never an unhandled-condition banner)."
  (format *error-output* "sel: ~?~%" control args)
  (finish-output *error-output*)
  (sb-ext:exit :code 2 :abort t))

(defun cannot-read (path)
  "A file that cannot be read -- missing, unreadable, or a directory: status 1."
  (format *error-output* "sel: cannot read ~a~%" path)
  (finish-output *error-output*)
  (sb-ext:exit :code 1 :abort t))

(defparameter +usage+
  "usage: sel -e EXPR       evaluate EXPR and print the result
       sel FILE          evaluate the program in FILE
       sel               read-eval-print loop on standard input
options:
       --deps            with -e or FILE: print the variables it reads, one per line
       --functions       list the function table
       -h, --help        this text
       --version         the package version
")

(defun package-version ()
  (asdf:component-version (asdf:find-system "sel-lang")))

(defun read-source-file (path)
  "The octets of PATH, or exit through CANNOT-READ. A directory opens on some
systems and reads as nothing; it is refused rather than run as an empty program."
  (let ((probe (ignore-errors (probe-file path))))
    (when (or (null probe) (null (pathname-name probe)) (uiop:directory-exists-p path))
      (cannot-read path)))
  (handler-case (read-file-octets path)
    (file-error () (cannot-read path))
    (stream-error () (cannot-read path))))

(defun stdin-tty-p ()
  (= 1 (sb-unix:unix-isatty 0)))

(defun parse-command-line (args)
  "(values expr-octets file-path want-deps) from the argument octets, exiting on
--help, --version, --functions and on misuse."
  (let ((expr nil) (file nil) (deps nil))
    (loop while args
          do (let* ((arg (pop args))
                    (flag (octets-ascii arg)))
               (cond
                 ((member flag '("-h" "--help") :test #'equal)
                  (write-string +usage+) (finish-output) (sb-ext:exit :code 0))
                 ((equal flag "--version")
                  (format t "sel ~a~%" (package-version)) (finish-output) (sb-ext:exit :code 0))
                 ((equal flag "--functions")
                  (format t "~{~a~%~}" (sel:function-names)) (finish-output) (sb-ext:exit :code 0))
                 ((equal flag "--deps") (setf deps t))
                 ((equal flag "-e")
                  (when (null args) (usage-error "-e needs an expression"))
                  ;; After a file (or a first -e) the expression is the
                  ;; second operand, and is named as the extra argument.
                  (when (or expr file) (usage-error "unexpected argument ~a" (octets-path (first args))))
                  (setf expr (pop args)))
                 ((and flag (> (length flag) 1) (char= (char flag 0) #\-))
                  (usage-error "unknown option ~a" flag))
                 ((or expr file)
                  (usage-error "unexpected argument ~a" (octets-path arg)))
                 (t (setf file (octets-path arg))))))
    (values expr file deps)))

(defun repl ()
  "One context for the whole session, so assignments persist. Read as octets and
decoded per line, so an invalid byte is E_UTF8 at its position rather than a
stream-decoding error. The prompt is written only to a terminal; on a pipe the
output is the results alone. A line of nothing but SEL whitespace (space, TAB,
CR, LF) is skipped; anything else, an NBSP-only line included, is evaluated."
  (let ((root (sel:make-none))
        (prompt (stdin-tty-p))
        (in (sb-sys:make-fd-stream 0 :input t :element-type '(unsigned-byte 8)
                                     :buffering :full)))
    (loop
      (when prompt (format t "sel> ") (finish-output))
      (let ((line (read-octet-line in)))
        (when (null line)
          (when prompt (format t "~%"))
          (return))
        (handler-case
            (let ((text (source-text line)))
              (when (plusp (length (trim-ws text)))
                (format t "~a~%" (render (sel:run (sel:compile-source text) root)))
                (finish-output)))
          (sel:sel-error (e) (report e)))))))

(defun main-1 ()
  (multiple-value-bind (expr file want-deps)
      (parse-command-line (mapcar #'argument-octets (script-args)))
    (let ((source (cond (expr expr)
                        (file (read-source-file file))
                        (t nil))))
      (if source
          (handler-case
              (let ((program (sel:compile-source (source-text source))))
                (if want-deps
                    (format t "~{~a~%~}" (sel:dependencies program))
                    (format t "~a~%" (render (sel:run program)))))
            (sel:sel-error (e) (report e) (finish-output) (sb-ext:exit :code 1)))
          (repl))))
  (finish-output)
  (sb-ext:exit :code 0))

(defun main ()
  ;; A reader that goes away (`sel --functions | head`) is not an error of ours.
  (handler-case (main-1)
    (sb-int:broken-pipe () (sb-ext:exit :code 0 :abort t))))
