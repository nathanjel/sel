;;;; API parity probe — Common Lisp. See tools/api.mjs for what this is and why.
;;;; The four drivers must stay in the same order with the same probe names; the
;;;; diff between their reports is the whole mechanism.

(in-package #:sel-cli)

(defvar *probes* '())
(defvar *probe-n* 0)

(defun say (name value)
  (push (format nil "~2,'0d ~a = ~a" (incf *probe-n*) name value) *probes*))

(defun yn (x) (if x "true" "false"))

;;; The kind values are keywords here; the report prints the spelling every host
;;; uses so the diff compares like with like.
(defun kind-name (k) (string-upcase (symbol-name k)))

(defun main ()
  (setf *probes* '() *probe-n* 0)

  ;; --- kind constants and predicates
  (say "kind.const.none" (kind-name :none))
  (say "kind.const.text" (kind-name :text))
  (say "kind.const.bin" (kind-name :bin))
  (say "kind.const.bool" (kind-name :bool))
  (say "kind.static.bool" (kind-name :bool))
  (say "kind.of.text" (kind-name (sel:value-kind (sel:evaluate "\"x\""))))
  (say "kind.of.bool" (kind-name (sel:value-kind (sel:evaluate "TRUE"))))
  (say "kind.of.none" (kind-name (sel:value-kind (sel:evaluate "(1,2)"))))
  (say "pred.isText" (yn (sel:value-text-p (sel:evaluate "\"x\""))))
  (say "pred.isBool" (yn (sel:value-bool-p (sel:evaluate "TRUE"))))
  (say "pred.isNone" (yn (sel:value-none-p (sel:evaluate "(1,2)"))))
  (say "pred.isBin" (yn (sel:value-bin-p (sel:evaluate "TO_UTF8(\"x\")"))))
  (say "pred.isText.on.bool" (yn (sel:value-text-p (sel:evaluate "TRUE"))))

  ;; --- constructors
  (say "ctor.text" (sel:value-dump (sel:make-text "hi")))
  (say "ctor.bool" (sel:value-dump (sel:make-bool t)))
  (say "ctor.none" (sel:value-dump (sel:make-none)))
  (say "ctor.num.canonicalises" (sel:value-dump (sel:make-num "007")))
  (say "ctor.int" (sel:value-dump (sel:make-int -3)))
  (say "ctor.list" (sel:value-dump (sel:make-list-value
                                    (list (sel:make-text "a") (sel:make-text "b")))))

  ;; --- children, and the ordering rules
  (let ((v (sel:make-none)))
    (sel:value-set v "b" (sel:make-text "1"))
    (sel:value-set v "a" (sel:make-text "2"))
    (say "children.size" (format nil "~d" (sel:value-size v)))
    (say "children.size.is.callable" (yn (and (fboundp 'sel:value-size) t)))
    (say "children.keys" (format nil "~{~a~^,~}" (sel:value-keys v)))
    (sel:value-set v "b" (sel:make-text "9"))
    (say "children.reassign.keeps.position" (format nil "~{~a~^,~}" (sel:value-keys v)))
    (say "children.reassign.no.growth" (format nil "~d" (sel:value-size v)))
    (say "children.has" (yn (sel:value-has v "a")))
    (say "children.has.missing" (yn (sel:value-has v "zz")))
    (say "children.get" (sel:value-dump (sel:value-get v "b"))))

  ;; --- scalar context
  (say "scalar.asText" (sel:as-text (sel:evaluate "\"héllo\"")))
  (say "scalar.asBool" (yn (sel:as-bool (sel:evaluate "TRUE"))))
  (say "scalar.takes.first.child" (sel:as-text (sel:evaluate "(7,8)")))
  (say "scalar.looksNumeric" (yn (sel:looks-numeric (sel:evaluate "\"2.50\""))))
  (say "scalar.looksNumeric.no" (yn (sel:looks-numeric (sel:evaluate "\"x\""))))

  ;; --- equality and dump
  (say "eql.same" (yn (sel:value-eql (sel:make-text "5") (sel:make-text "5"))))
  (say "eql.not.normalised" (yn (sel:value-eql (sel:make-text "5.00") (sel:make-text "5"))))
  (say "dump.tree" (sel:value-dump (sel:evaluate "A=1; A[2]=\"x\"; A")))

  ;; --- programs
  (let ((p (sel:compile-source "IF(A > B, A, C)")))
    (say "program.dependencies" (format nil "~{~a~^ ~}" (sel:dependencies p))))
  (say "program.deps.excludes.assigned"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "X = 1; X + Y"))))
  (say "program.deps.excludes.binder"
       (format nil "~{~a~^ ~}" (sel:dependencies (sel:compile-source "ALL(I, IT, IT > 0)"))))
  (let ((ctx (sel:make-none)))
    (sel:value-set ctx "TOTAL" (sel:make-num "59.97"))
    (say "program.run.reads.context" (sel:value-dump (sel:evaluate "TOTAL > 10.00" ctx)))
    (sel:evaluate "SEEN = TOTAL * 2" ctx)
    (say "program.run.mutates.context" (sel:as-text (sel:value-get ctx "SEEN"))))
  (say "registry.count" (format nil "~d" (length (sel:function-names))))
  (say "registry.sorted.first" (first (sel:function-names)))

  ;; --- errors
  (handler-case (sel:evaluate (format nil "1 +~%  X"))
    (sel:sel-error (e)
      (say "error.code" (sel:sel-error-code e))
      (say "error.line" (format nil "~d" (sel:sel-error-line e)))
      (say "error.col" (format nil "~d" (sel:sel-error-col e)))
      (say "error.isSelError" (yn (typep e 'sel:sel-error)))))
  (handler-case (sel:compile-source "NOPE(1)")
    (sel:sel-error (e) (say "error.compile.unknown.func" (sel:sel-error-code e))))
  (handler-case (sel:make-num "x")
    (sel:sel-error (e) (say "error.host.badnum" (sel:sel-error-code e))))

  (format t "~{~a~%~}" (reverse *probes*))
  (sb-ext:exit :code 0))
