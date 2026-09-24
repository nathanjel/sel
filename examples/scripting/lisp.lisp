;;;; Scripting an application's behaviour — functions the host provides, from Common Lisp.
;;;;
;;;;   sbcl --noinform --disable-debugger --non-interactive \
;;;;        --load lisp/bin/boot.lisp --load examples/scripting/lisp.lisp \
;;;;        --eval '(sel-example:main)'
;;;;
;;;; SEL has no way to reach the world on its own, and that is the point: the
;;;; application decides what a script may touch by registering functions. Here
;;;; the warehouse gets four — STOCK and WEIGHT to read the catalogue, RESERVE to
;;;; take stock, NOTIFY to queue a message — and fulfil.sel, a file the warehouse
;;;; team owns, decides per order whether to ship, how, and whom to tell. The
;;;; application stays the same when the policy changes.
;;;;
;;;; A registered function is strict: its arguments arrive evaluated, left to
;;;; right, through the same typed readers the builtins use, so a script passing
;;;; the wrong kind gets the usual error at the usual position.
;;;;
;;;; The four files beside this one print byte-identical output.

(defpackage #:sel-example
  (:use #:common-lisp)
  (:export #:main))

(in-package #:sel-example)

(defparameter *here* (make-pathname :name nil :type nil :defaults *load-truename*)
  "This file's directory: fulfil.sel sits beside it.")

(defun table (&rest pairs)
  "An EQUAL hash table from (key . value) pairs."
  (let ((h (make-hash-table :test #'equal)))
    (loop for (k . v) in pairs do (setf (gethash k h) v))
    h))

(defparameter *inventory* (table '("LAMP-01" . 4) '("DESK-02" . 1) '("CHAIR-03" . 6)))
(defparameter *weights* (table '("LAMP-01" . "1.6") '("DESK-02" . "28.0") '("CHAIR-03" . "7.5")))
(defparameter *outbox* '())

;; EXAMPLE-BEGIN register
(defun stock (args)
  (sel:make-int (gethash (sel:args-text args 0) *inventory* 0)))

(defun reserve (args)
  (let ((sku (sel:args-text args 0))
        (qty (sel:args-non-neg-int args 1)))
    (cond ((< (gethash sku *inventory* 0) qty)
           (sel:make-bool nil))
          (t
           (decf (gethash sku *inventory*) qty)
           (sel:make-bool t)))))

(defun weight (args)
  (sel:make-text (gethash (sel:args-text args 0) *weights* "0")))

(defun notify (args)
  (setf *outbox* (append *outbox* (list (format nil "~a: ~a"
                                                (sel:args-text args 0)
                                                (sel:args-text args 1)))))
  (sel:make-bool t))

(sel:register-function "STOCK" 1 1 #'stock)
(sel:register-function "RESERVE" 2 2 #'reserve)
(sel:register-function "WEIGHT" 1 1 #'weight)
(sel:register-function "NOTIFY" 2 2 #'notify)
;; EXAMPLE-END register

(defun main ()
  ;; EXAMPLE-BEGIN run
  (let ((fulfil (sel:compile-source      ; after registering: names resolve now
                 (uiop:read-file-string (merge-pathnames "fulfil.sel" *here*)
                                        :external-format :utf-8))))
    (format t "1. the script reads ~{~a~^, ~}~%" (sel:dependencies fulfil))
    (format t "2. orders~%")
    (dolist (order '((("ORDER" . "A-1") ("COUNTRY" . "PL")
                      ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "2"))
                                  (("sku" . "CHAIR-03") ("qty" . "1")))))
                     (("ORDER" . "A-2") ("COUNTRY" . "DE")
                      ("ITEMS" . ((("sku" . "DESK-02") ("qty" . "1"))
                                  (("sku" . "CHAIR-03") ("qty" . "2")))))
                     (("ORDER" . "A-3") ("COUNTRY" . "PL")
                      ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "3")))))
                     (("ORDER" . "A-4") ("COUNTRY" . "PL")
                      ("ITEMS" . ((("sku" . "LAMP-01") ("qty" . "two")))))))
      (let ((decision
              (handler-case (sel:as-text (sel:run fulfil (sel:from-native order)))
                (sel:sel-error (e)
                  (format nil "~a at ~D:~D"
                          (sel:sel-error-code e) (sel:sel-error-line e) (sel:sel-error-col e))))))
        (format t "   ~a  ~a~%" (cdr (assoc "ORDER" order :test #'string=)) decision))))
  ;; EXAMPLE-END run

  (format t "3. outbox~%")
  (dolist (message *outbox*)
    (format t "   ~a~%" message))
  (format t "4. stock left~%")
  (dolist (sku (sort (loop for sku being the hash-keys of *inventory* collect sku) #'string<))
    (format t "   ~9a ~D~%" sku (gethash sku *inventory*)))
  0)
