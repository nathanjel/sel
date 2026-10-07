;;;; SEL — Simple Expression Language, Common Lisp implementation.
;;;;
;;;;   (ql:quickload :sel-lang)
;;;;   (sel:evaluate "1 + 2")
;;;;
;;;; The language is specified in spec/SPEC.md, which is normative: where this
;;;; implementation and that document disagree, this implementation is wrong.
;;;;
;;;; Developed and tested on SBCL. The one portability assumption that matters is
;;;; that CL characters are Unicode code points, which is what makes every
;;;; length, position and offset SEL reports come out right without a separate
;;;; code point layer. That holds on SBCL, CCL, ECL and CLISP; it would not hold
;;;; on an implementation with UTF-16 strings.

;;; The hot files are compiled at (speed 3), and SBCL explains every optimisation
;;; it could not make in a compiler NOTE -- several hundred per build, among which
;;; a real WARNING was easy to miss. Notes are muffled while these systems
;;; compile, and only then (no global proclamation reaches the caller's code);
;;; warnings and style warnings still print, and lisp/bin/check-load fails on a
;;; warning.
(defun call-muffling-compiler-notes (thunk)
  (handler-bind (#+sbcl (sb-ext:compiler-note #'muffle-warning))
    (funcall thunk)))

(defsystem "sel-lang"
  :around-compile call-muffling-compiler-notes
  :description "A small expression language for business rules: one rule gives the same answer in Python, JavaScript, PHP, C++23, Common Lisp, Rust and Go, or in your database as SQL. Exact decimal arithmetic, no floating point, no truthiness."
  :author "Marcin Gałczyński"
  :license "MIT"
  :version "0.10.3"
  :homepage "https://github.com/nathanjel/sel"
  :source-control (:git "https://github.com/nathanjel/sel.git")
  :depends-on ("cl-ppcre")
  :serial t
  :components
  ((:module "src"
    :serial t
    :components
    ((:file "package")
     (:file "limits")
     (:file "errors")
     (:file "utf8")
     (:file "decimal")
     (:file "value")
     (:file "registry")
     (:file "builtin-manifest")
     (:file "math-ops")
     (:file "lexicon")
     (:file "lexer")
     (:file "parser")
     (:file "math-plan")
     (:file "eval")
     (:module "builtins"
      :serial t
      :components ((:file "control")
                   (:file "structure")
                   (:file "aggregate")
                   (:file "text")
                   (:file "number")
                   (:file "binary")
                   (:file "regex")
                   (:file "null")))
     (:file "optimizer")
     (:file "sel"))))
  :in-order-to ((test-op (test-op "sel-lang/tests"))))

;;; The SEL->SQL layer. A separate system, for the reason the JS host keeps its
;;; translator behind a separate entry point: a program that only wants the
;;; evaluator should not carry the dialect map. `(ql:quickload :sel-lang/sql)`
;;; brings in both.
(defsystem "sel-lang/sql"
  :around-compile call-muffling-compiler-notes
  :description "SEL -> SQL translation"
  :author "Marcin Gałczyński"
  :license "MIT"
  ;; No :version of its own: it ships with, and is versioned as, "sel-lang"
  ;; (the one :version tools/check-version.sh reads).
  :depends-on ("sel-lang")
  :serial t
  :components
  ((:module "src/sql"
    :serial t
    :components
    ((:file "package")
     (:file "errors")
     (:file "map-data")
     (:file "map")
     (:file "fragment")
     (:file "relational-plan")
     (:file "emit")
     (:file "binding")
     (:file "stage1")
     (:file "translator")
     (:file "hybrid")))))

(defsystem "sel-lang/tests"
  :description "Unit tests for the layers underneath the conformance suite"
  :author "Marcin Gałczyński"
  :license "MIT"
  ;; The SQL layer too: two of its failure modes -- a dialect that is not a
  ;; string, and a caller's rebound *PRINT-BASE* -- are reachable only from host
  ;; code, so the .sqlt corpus cannot express them and only a test here can.
  :depends-on ("sel-lang" "sel-lang/sql" "fiveam")
  :serial t
  :components
  ((:module "tests"
    :serial t
    :components ((:file "package")
                 (:file "unit"))))
  :perform (test-op (op system)
             (unless (symbol-call :fiveam :run! (find-symbol* :sel :sel-tests))
               (error "SEL unit tests failed"))))
