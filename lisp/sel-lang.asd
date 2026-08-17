;;;; SEL — Simple Expression Language, Common Lisp implementation.
;;;;
;;;;   (ql:quickload :sel)
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

(defsystem "sel-lang"
  :description "SEL — a small expression language that evaluates identically on every host"
  :author "Marcin Gałczyński"
  :license "MIT"
  :version "0.1.1"
  :homepage "https://github.com/nathanjel/sel"
  :source-control (:git "https://github.com/nathanjel/sel.git")
  :depends-on ("cl-ppcre")
  :serial t
  :components
  ((:module "src"
    :serial t
    :components
    ((:file "package")
     (:file "errors")
     (:file "utf8")
     (:file "decimal")
     (:file "value")
     (:file "registry")
     (:file "lexer")
     (:file "parser")
     (:file "eval")
     (:module "builtins"
      :serial t
      :components ((:file "control")
                   (:file "structure")
                   (:file "aggregate")
                   (:file "text")
                   (:file "number")
                   (:file "binary")
                   (:file "regex")))
     (:file "sel"))))
  :in-order-to ((test-op (test-op "sel-lang/tests"))))

(defsystem "sel-lang/tests"
  :description "Unit tests for the layers underneath the conformance suite"
  :author "Marcin Gałczyński"
  :license "MIT"
  :depends-on ("sel-lang" "fiveam")
  :serial t
  :components
  ((:module "tests"
    :serial t
    :components ((:file "package")
                 (:file "unit"))))
  :perform (test-op (op system)
             (unless (symbol-call :fiveam :run! (find-symbol* :sel :sel-tests))
               (error "SEL unit tests failed"))))
