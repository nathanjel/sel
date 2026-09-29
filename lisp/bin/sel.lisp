;;;; SEL command line: evaluate an expression, a file, or start a REPL.
;;;;
;;;;   sel -e 'EXPR'          evaluate and print
;;;;   sel file.sel           evaluate a file
;;;;   sel --deps -e 'EXPR'   print the variables the expression reads
;;;;   sel --functions        list the function table
;;;;   sel                    REPL, keeping one context across lines

(in-package #:sel-cli)

(defun show (v)
  (if (zerop (sel:value-size v))
      (case (sel:value-kind v)
        (:text (sel::value-scalar v))
        (:bool (sel:value-dump v))
        (:bin (concatenate 'string "bin:" (subseq (sel:value-dump v) 1)))
        (t (sel:value-dump v)))
      (sel:value-dump v)))

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

(defun main-1 ()
  (let* ((all (mapcar #'argument-octets (script-args)))
         (want-deps (find "--deps" all :key #'octets-ascii :test #'equal))
         (args (remove "--deps" all :key #'octets-ascii :test #'equal)))

    (let ((flag (and (first args) (octets-ascii (first args)))))
      (when (and (equal flag "-e") (null (second args)))
        (usage-error "-e needs an expression"))
      (when (and flag (> (length flag) 1) (char= (char flag 0) #\-)
                 (not (member flag '("-e" "--functions") :test #'string=)))
        (usage-error "unknown option ~a" flag)))

    (when (equal (and (first args) (octets-ascii (first args))) "--functions")
      (format t "~{~a~%~}" (sel:function-names))
      (sb-ext:exit :code 0))

    (let ((source (cond ((and (equal (and (first args) (octets-ascii (first args))) "-e")
                              (second args))
                         (second args))
                        ((first args)
                         (handler-case (read-file-octets (octets-path (first args)))
                           (file-error ()
                             (usage-error "cannot read ~a" (octets-path (first args))))
                           (stream-error ()
                             (usage-error "cannot read ~a" (octets-path (first args))))))
                        (t nil))))
      (if source
          (handler-case
              (let ((program (sel:compile-source (source-text source))))
                (if want-deps
                    (format t "~{~a~%~}" (sel:dependencies program))
                    (format t "~a~%" (show (sel:run program)))))
            (sel:sel-error (e) (report e) (sb-ext:exit :code 1)))
          ;; REPL: one context for the whole session, so assignments persist.
          ;; Read as octets and decoded per line, so an invalid byte is E_UTF8 at its
          ;; position rather than a stream-decoding error.
          (let ((root (sel:make-none))
                (in (sb-sys:make-fd-stream 0 :input t :element-type '(unsigned-byte 8)
                                             :buffering :full)))
            (loop
              (format t "sel> ")
              (finish-output)
              (let ((line (read-octet-line in)))
                (when (null line) (format t "~%") (return))
                (handler-case
                    (let ((text (source-text line)))
                      (when (plusp (length (trim-ws text)))
                        (format t "~a~%" (show (sel:run (sel:compile-source text) root)))))
                  (sel:sel-error (e) (report e))))))))
    (sb-ext:exit :code 0)))

(defun main ()
  ;; A reader that goes away (`sel --functions | head`) is not an error of ours.
  (handler-case (main-1)
    (sb-int:broken-pipe () (sb-ext:exit :code 0 :abort t))))
