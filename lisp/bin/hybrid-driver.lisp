;;;; This host's side of tools/check-hybrid-parity-driver.py, the executing
;;;; hybrid-parity lane: the corpus in sql/oracle/hybrid.json is planned and
;;;; run HERE, and the harness supplies the one thing this host cannot: a
;;;; database. One JSON request per line on stdin, one JSON answer per line on
;;;; stdout.
;;;;
;;;;   {"op":"init","bindings":{"ORDERS":{"from":"orders","alias":"o",
;;;;                                     "fields":{"ID":{"column":"id","table":"o","type":"NUM"}}}}}
;;;;   {"op":"run","sel":"...","vars":{...}}
;;;;       -> {"status":"ok","native":<json>,"keys":[...]} | {"status":"err","code":"E_X","line":1,"col":2}
;;;;   {"op":"plan","sel":"...","dialect":"sqlite"}
;;;;       -> {"kind":"pure_sql|hybrid|pure_memory","statement":"...","params":[...]}
;;;;   {"op":"execute","sel":"...","dialect":"sqlite","vars":{...},"rows":<json>}
;;;;       -> the answer of `run`, plus "before"/"after": the dump of the caller's
;;;;          context around the call
;;;;
;;;; A value crosses as JSON: an object or array is a value with children (an
;;;; array's keys are 1..n), a string is text, a number is a SEL number spelt as
;;;; written, and null is none. Everything comes back as text, which is what every
;;;; value is here.

(let ((*standard-output* (make-broadcast-stream)))
  (funcall (find-symbol "QUICKLOAD" "QL") :sel-lang/sql))

(in-package #:sel-cli)

;;; --- a small JSON reader and writer -----------------------------------------

(defun json-fail (msg) (error "hybrid-driver: bad JSON: ~a" msg))

(defun json-parse (text)
  "Objects become (:obj (key . value) ...), arrays (:arr value ...); numbers are
kept as their text, wrapped (:num text); true, false and null are :true, :false
and :null."
  (let ((i 0) (n (length text)))
    (labels ((peek () (and (< i n) (char text i)))
             (skip () (loop while (and (< i n) (member (char text i) '(#\Space #\Tab #\Newline #\Return)))
                            do (incf i)))
             (expect (c) (skip) (unless (eql (peek) c) (json-fail (format nil "expected ~a at ~a" c i))) (incf i))
             (str ()
               (expect #\")
               (with-output-to-string (out)
                 (loop
                   (let ((c (peek)))
                     (unless c (json-fail "unterminated string"))
                     (incf i)
                     (cond ((char= c #\") (return))
                           ((char= c #\\)
                            (let ((e (peek)))
                              (incf i)
                              (case e
                                (#\n (write-char #\Newline out)) (#\t (write-char #\Tab out))
                                (#\r (write-char #\Return out)) (#\b (write-char #\Backspace out))
                                (#\f (write-char #\Page out))
                                (#\u (let ((cp (parse-integer text :start i :end (+ i 4) :radix 16)))
                                       (incf i 4)
                                       ;; a surrogate pair is one code point
                                       (when (and (<= #xD800 cp #xDBFF) (< (+ i 5) n)
                                                  (char= (char text i) #\\) (char= (char text (1+ i)) #\u))
                                         (let ((lo (parse-integer text :start (+ i 2) :end (+ i 6) :radix 16)))
                                           (incf i 6)
                                           (setf cp (+ #x10000 (ash (- cp #xD800) 10) (- lo #xDC00)))))
                                       (write-char (code-char cp) out)))
                                (t (write-char e out)))))
                           (t (write-char c out)))))))
             (val ()
               (skip)
               (let ((c (peek)))
                 (cond ((eql c #\{)
                        (incf i) (skip)
                        (if (eql (peek) #\}) (progn (incf i) (list :obj))
                            (let ((cells '()))
                              (loop (skip)
                                    (let ((k (str))) (expect #\:) (push (cons k (val)) cells))
                                    (skip)
                                    (cond ((eql (peek) #\,) (incf i))
                                          ((eql (peek) #\}) (incf i) (return))
                                          (t (json-fail "object"))))
                              (cons :obj (nreverse cells)))))
                       ((eql c #\[)
                        (incf i) (skip)
                        (if (eql (peek) #\]) (progn (incf i) (list :arr))
                            (let ((items '()))
                              (loop (push (val) items) (skip)
                                    (cond ((eql (peek) #\,) (incf i))
                                          ((eql (peek) #\]) (incf i) (return))
                                          (t (json-fail "array"))))
                              (cons :arr (nreverse items)))))
                       ((eql c #\") (str))
                       ((and (< (+ i 3) n) (string= "true" text :start2 i :end2 (+ i 4))) (incf i 4) :true)
                       ((and (< (+ i 4) n) (string= "false" text :start2 i :end2 (+ i 5))) (incf i 5) :false)
                       ((and (< (+ i 3) n) (string= "null" text :start2 i :end2 (+ i 4))) (incf i 4) :null)
                       (t (let ((s i))
                            (loop while (and (< i n) (find (char text i) "+-0123456789.eE")) do (incf i))
                            (when (= s i) (json-fail (format nil "value at ~a" i)))
                            (list :num (subseq text s i))))))))
      (prog1 (val) (skip)))))

(defun json-write (x out)
  (cond ((stringp x)
         (write-char #\" out)
         (loop for c across x
               do (let ((code (char-code c)))
                    (cond ((char= c #\") (write-string "\\\"" out))
                          ((char= c #\\) (write-string "\\\\" out))
                          ((char= c #\Newline) (write-string "\\n" out))
                          ((< code 32) (format out "\\u~4,'0x" code))
                          (t (write-char c out)))))
         (write-char #\" out))
        ((integerp x) (format out "~D" x))
        ((eq x :true) (write-string "true" out))
        ((eq x :false) (write-string "false" out))
        ((eq x :null) (write-string "null" out))
        ((and (consp x) (eq (car x) :obj))
         (write-char #\{ out)
         (loop for (cell . more) on (cdr x)
               do (json-write (car cell) out) (write-char #\: out) (json-write (cdr cell) out)
                  (when more (write-char #\, out)))
         (write-char #\} out))
        ((listp x)
         (write-char #\[ out)
         (loop for (item . more) on x
               do (json-write item out) (when more (write-char #\, out)))
         (write-char #\] out))
        (t (json-fail (format nil "cannot write ~s" x)))))

(defun obj-get (obj key) (cdr (assoc key (cdr obj) :test #'string=)))

;;; --- values, in and out -----------------------------------------------------

(defun json->value (j)
  (cond ((stringp j) (sel:make-text j))
        ((eq j :true) (sel:make-bool t))
        ((eq j :false) (sel:make-bool nil))
        ((eq j :null) (sel:make-none))
        ((and (consp j) (eq (car j) :num)) (sel:make-num (second j)))
        ((and (consp j) (eq (car j) :obj))
         (let ((v (sel:make-none)))
           (dolist (cell (cdr j) v) (sel:value-set v (car cell) (json->value (cdr cell))))))
        ((and (consp j) (eq (car j) :arr))
         (let ((v (sel:make-none)))
           (loop for item in (cdr j) for i from 1
                 do (sel:value-set v (format nil "~D" i) (json->value item)))
           v))
        (t (json-fail "value"))))

(defun value->json (v)
  (cond ((plusp (sel:value-size v))
         (cons :obj (mapcar (lambda (k) (cons k (value->json (sel:value-get v k))))
                            (sel:value-keys v))))
        ((sel:value-none-p v) (list :obj))
        ((sel:value-bool-p v) (if (eq t (sel::value-scalar v)) "TRUE" "FALSE"))
        (t (sel:as-text v))))

;;; --- the operations ---------------------------------------------------------

(defvar *bindings* '())

(defun kind-of (name)
  (cond ((string-equal name "NUM") :num) ((string-equal name "TEXT") :text)
        ((string-equal name "BOOL") :bool) ((string-equal name "BIN") :bin)
        (t :unknown)))

(defun init-bindings (spec)
  (setf *bindings*
        (loop for (name . b) in (cdr spec)
              collect (cons name
                            (sel.sql:binding-relation
                             (obj-get b "from") (obj-get b "alias")
                             (loop for (fname . c) in (cdr (obj-get b "fields"))
                                   collect (cons fname (sel.sql:binding-column
                                                        (obj-get c "column") (obj-get c "table")
                                                        (kind-of (obj-get c "type"))))))))))

(defun outcome (thunk)
  "The answer of a SEL evaluation as an :obj, or the error it raised."
  (handler-case
      (let ((v (funcall thunk)))
        (list :obj (cons "status" "ok")
              (cons "native" (value->json v))
              (cons "keys" (sel:value-keys v))))
    (sel:sel-error (e)
      (list :obj (cons "status" "err") (cons "code" (sel:sel-error-code e))
            (cons "line" (sel:sel-error-line e)) (cons "col" (sel:sel-error-col e))))
    (sel.sql:sql-error (e)
      (list :obj (cons "status" "err") (cons "code" (format nil "SQL:~a" (sel.sql:sql-error-code e)))
            (cons "line" 0) (cons "col" 0)))
    (error (e)
      (list :obj (cons "status" "err") (cons "code" (format nil "HOST:~a" (type-of e)))
            (cons "line" 0) (cons "col" 0)))))

(defun handle (req)
  (let ((op (obj-get req "op")))
    (cond
      ((string= op "init") (init-bindings (obj-get req "bindings")) (list :obj (cons "status" "ok")))
      ((string= op "run")
       (outcome (lambda ()
                  (sel:run (sel:compile-source (obj-get req "sel"))
                           (json->value (or (obj-get req "vars") (list :obj)))))))
      ((string= op "plan")
       (handler-case
           (let* ((plan (sel.sql:plan-hybrid (sel:compile-source (obj-get req "sel"))
                                             (obj-get req "dialect") *bindings*))
                  (frag (sel.sql::hybrid-plan-sql-statement plan)))
             (list :obj
                   (cons "kind" (cond ((sel.sql:hybrid-plan-pure-sql-p plan) "pure_sql")
                                      ((sel.sql:hybrid-plan-pure-memory-p plan) "pure_memory")
                                      (t "hybrid")))
                   (cons "statement" (if frag (sel.sql:as-statement frag) "-"))))
         (error (e) (list :obj (cons "status" "err") (cons "code" (format nil "HOST:~a" e))))))
      ((string= op "execute")
       (let* ((plan (sel.sql:plan-hybrid (sel:compile-source (obj-get req "sel"))
                                         (obj-get req "dialect") *bindings*))
              (caller (json->value (or (obj-get req "vars") (list :obj))))
              (before (sel:value-dump caller))
              (rows (json->value (obj-get req "rows")))
              (res (outcome (lambda ()
                              (sel.sql:execute-hybrid plan (lambda (sql params) (declare (ignore sql params)) rows)
                                                      caller)))))
         (append res (list (cons "before" before) (cons "after" (sel:value-dump caller))))))
      ((string= op "plan-statement")
       ;; The SQL the plan sends, in :params mode, with the values it binds -- what
       ;; the harness runs on its database.
       (let* ((plan (sel.sql:plan-hybrid (sel:compile-source (obj-get req "sel"))
                                         (obj-get req "dialect") *bindings*))
              (frag (sel.sql::hybrid-plan-sql-statement plan)))
         (if frag
             (list :obj (cons "sql" (sel.sql:as-statement frag :params))
                   (cons "params" (mapcar (lambda (v) (if (sel:value-none-p v) :null (sel:as-text v)))
                                          (sel.sql:bindings frag))))
             (list :obj (cons "sql" :null) (cons "params" nil)))))
      (t (list :obj (cons "status" "err") (cons "code" "HOST:unknown-op"))))))

(defun main ()
  (loop for line = (read-line *standard-input* nil nil)
        while line
        do (let ((answer (handler-case (handle (json-parse line))
                           (error (e) (list :obj (cons "status" "err")
                                            (cons "code" (format nil "HOST:~a" e)))))))
             (json-write answer *standard-output*)
             (terpri *standard-output*)
             (finish-output *standard-output*)))
  (sb-ext:exit :code 0))
