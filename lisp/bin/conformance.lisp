;;;; Conformance runner. The suite in conformance/ is normative; this program has
;;;; no opinions of its own beyond the file format in conformance/README.md.
;;;;
;;;; Note that expectation strings are unescaped by this file's own tiny escape
;;;; reader, not by SEL's lexer — the suite must not validate the lexer with the
;;;; lexer.

(in-package #:sel-cli)

(defstruct tcase
  (name "") (at "") (has-setup nil) (setup "") (source "") (expect ""))

(defun parse-selt (text file)
  (let ((cases '())
        (cur nil)
        (section nil)
        (setup '()) (source '()) (expect '())
        (idx 0))
    (flet ((finish ()
             (when cur
               (setf (tcase-setup cur) (trim-ws (join-lines setup))
                     (tcase-source cur) (trim-ws (join-lines source))
                     (tcase-expect cur) (trim-ws (join-lines expect)))
               (push cur cases))))
      (dolist (line (split-lines text))
        (incf idx)
        (let ((at (format nil "~a:~d" file idx)))
          (cond
            ((starts-with "### " line)
             (finish)
             (let ((rest (trim-ws (subseq line 4))))
               (unless (starts-with "name:" rest)
                 (error "~a: malformed case header" at))
               (setf cur (make-tcase :name (trim-ws (subseq rest 5)) :at at)
                     section nil setup '() source '() expect '())))

            ((string= line "===") (finish) (setf cur nil section nil))

            ((starts-with "--- " line)
             (unless cur (error "~a: section outside a case" at))
             (setf section (trim-ws (subseq line 4)))
             (cond ((string= section "setup") (setf (tcase-has-setup cur) t))
                   ((string= section "source"))
                   ((string= section "expect"))
                   ((string= section "note"))
                   (t (error "~a: unknown section ~a" at section))))

            ((and cur section)
             (cond ((string= section "setup") (push line setup))
                   ((string= section "source") (push line source))
                   ((string= section "expect") (push line expect)))))))
      (finish))
    (nreverse cases)))

;;; The runner's own escape set, deliberately not SEL's.
(defun unescape (lit at)
  (unless (and (>= (length lit) 2)
               (char= (char lit 0) #\")
               (char= (char lit (1- (length lit))) #\"))
    (error "~a: expected a quoted string, got ~a" at lit))
  (let ((body (subseq lit 1 (1- (length lit)))))
    (with-output-to-string (out)
      (let ((i 0))
        (loop while (< i (length body))
              do (let ((c (char body i)))
                   (if (char/= c #\\)
                       (progn (write-char c out) (incf i))
                       (let ((e (char body (1+ i))))
                         (case e
                           (#\\ (write-char #\\ out) (incf i 2))
                           (#\" (write-char #\" out) (incf i 2))
                           (#\n (write-char #\Newline out) (incf i 2))
                           (#\t (write-char #\Tab out) (incf i 2))
                           (#\r (write-char #\Return out) (incf i 2))
                           (#\u (write-char (code-char (parse-integer body :start (+ i 2)
                                                                           :end (+ i 6)
                                                                           :radix 16))
                                            out)
                                (incf i 6))
                           (t (error "~a: bad escape \\~a" at e)))))))))))

;;; Describes an actual result in the same vocabulary the expectations use, so a
;;; failure report reads as "want X, got Y" in one language.
(defun describe-value (v)
  (if (plusp (sel:value-size v))
      (format nil "tree ~a" (sel:value-dump v))
      (case (sel:value-kind v)
        (:text (format nil "text ~a" (subseq (sel:value-dump v) 1)))
        (:bin (format nil "bin ~a" (subseq (sel:value-dump v) 1)))
        (:bool (format nil "bool ~a" (sel:value-dump v)))
        (t "none"))))

(defun check-expectation (expect value err at)
  "Returns NIL when the expectation holds, or a problem string."
  (let* ((space (position #\Space expect))
         (form (if space (subseq expect 0 space) expect))
         (rest (if space (trim-ws (subseq expect (1+ space))) "")))
    (cond
      ((string= form "error")
       (if (null err)
           (format nil "expected ~a, got value ~a" expect (describe-value value))
           (let* ((parts (loop with start = 0
                               for p = (position #\Space rest :start start)
                               collect (subseq rest start p)
                               while p do (setf start (1+ p))))
                  (want-code (first parts))
                  (where (third parts)))
             (cond
               ((string/= (sel:sel-error-code err) want-code)
                (format nil "expected ~a, got ~a (~a)" want-code
                        (sel:sel-error-code err) (sel:sel-error-message err)))
               ((and where (string= (second parts) "at")
                     (string/= (format nil "~d:~d"
                                       (sel:sel-error-line err) (sel:sel-error-col err))
                               where))
                (format nil "expected ~a at ~a, got it at ~d:~d" want-code where
                        (sel:sel-error-line err) (sel:sel-error-col err)))
               (t nil)))))

      (err (format nil "expected ~a, got ~a (~a)" expect
                   (sel:sel-error-code err) (sel:sel-error-message err)))

      ((string= form "text")
       (if (or (not (eq (sel:value-kind value) :text)) (plusp (sel:value-size value)))
           (format nil "wanted text, got ~a" (describe-value value))
           (if (string= (sel::value-scalar value) (unescape rest at))
               nil
               (format nil "got ~a" (describe-value value)))))

      ((string= form "num")
       (if (or (not (eq (sel:value-kind value) :text)) (plusp (sel:value-size value)))
           (format nil "wanted a number, got ~a" (describe-value value))
           (if (string= (sel::value-scalar value) rest)
               nil
               (format nil "got ~a" (describe-value value)))))

      ((string= form "bin")
       (if (or (not (eq (sel:value-kind value) :bin)) (plusp (sel:value-size value)))
           (format nil "wanted binary, got ~a" (describe-value value))
           (if (string= (subseq (sel:value-dump value) 1) rest)
               nil
               (format nil "got ~a" (describe-value value)))))

      ((string= form "bool")
       (if (or (not (eq (sel:value-kind value) :bool)) (plusp (sel:value-size value)))
           (format nil "wanted a boolean, got ~a" (describe-value value))
           (if (string= (sel:value-dump value) rest)
               nil
               (format nil "got ~a" (describe-value value)))))

      ((string= form "none")
       (if (and (eq (sel:value-kind value) :none) (zerop (sel:value-size value)))
           nil
           (format nil "got ~a" (describe-value value))))

      ((string= form "tree")
       (if (string= (sel:value-dump value) rest)
           nil
           (format nil "got tree ~a" (sel:value-dump value))))

      (t (error "~a: unknown expectation form ~a" at form)))))

(defun run-case (c)
  "Returns (values value error suite-error)."
  (let ((root (sel:make-none)))
    (when (and (tcase-has-setup c) (plusp (length (tcase-setup c))))
      (handler-case (sel:run (sel:compile-source (tcase-setup c)) root)
        ;; A broken setup is a suite bug, not a failing implementation.
        (sel:sel-error (e) (return-from run-case (values nil nil (princ-to-string e))))))
    (handler-case (values (sel:run (sel:compile-source (tcase-source c)) root) nil nil)
      (sel:sel-error (e) (values nil e nil)))))

(defun main ()
  (let* ((args (script-args))
         (files (or args
                    (sort (mapcar #'namestring (directory "conformance/*.selt")) #'string<)))
         (pass 0)
         (failures '())
         (suite-errors '())
         (seen (make-hash-table :test #'equal)))
    (dolist (file files)
      (let ((shortname (file-namestring file)))
        (handler-case
            (dolist (c (parse-selt (read-text-file file) shortname))
              (let ((previous (gethash (tcase-name c) seen)))
                (if previous
                    (push (format nil "~a: duplicate case name ~a (also ~a)"
                                  (tcase-at c) (tcase-name c) previous)
                          suite-errors)
                    (progn
                      (setf (gethash (tcase-name c) seen) (tcase-at c))
                      (multiple-value-bind (value err suite-error) (run-case c)
                        (if suite-error
                            (push (format nil "~a: ~a: setup failed: ~a"
                                          (tcase-at c) (tcase-name c) suite-error)
                                  suite-errors)
                            (let ((problem (check-expectation (tcase-expect c) value err
                                                              (tcase-at c))))
                              (if problem
                                  (push (cons c problem) failures)
                                  (incf pass)))))))))
          (error (e) (push (princ-to-string e) suite-errors)))))

    (dolist (f (reverse failures))
      (format t "FAIL ~a  (~a)~%" (tcase-name (car f)) (tcase-at (car f)))
      (format t "     source: ~a~%" (tcase-source (car f)))
      (format t "     want:   ~a~%" (tcase-expect (car f)))
      (format t "     ~a~%" (cdr f)))
    (dolist (e (reverse suite-errors))
      (format t "SUITE ~a~%" e))

    (format t "~%~d passed, ~d failed, ~d suite errors~%"
            pass (length failures) (length suite-errors))
    (sb-ext:exit :code (if (and (null failures) (null suite-errors)) 0 1))))
