(defpackage #:sel-tests
  (:use #:common-lisp #:fiveam)
  (:export #:sel))

(in-package #:sel-tests)

(def-suite sel :description "SEL — the layers underneath the conformance suite")
